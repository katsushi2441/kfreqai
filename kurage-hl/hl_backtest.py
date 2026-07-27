"""kfreqaihl バックテスト — 共通コア(strategy_core)を実Hyperliquid履歴で再生する。

kfreqai(freqtrade)の `backtesting` に相当するもの。ただしfreqtradeは1口座・
現物前提なので流用できず、hl_engine.py の本番ループ(枠/ストップ/ピークトレール/
両建て)を**そのまま履歴の上で回す**ポートフォリオ・バックテスタを自作している。
本番と同じ strategy_core / 同じ決済判断を使うので、「本番でやってることを過去で
試す」という意味が保たれる(頭脳は1つ)。

設計上の要点:
- ローソク足は公開API(fetch_candles)から取得。資金もウォレットも不要。
- ピーク(利確トレール)管理は tenant_store を汚さないようローカル辞書で持つ
  (本番はsqliteの position_state だが、バックテストは副作用ゼロにする)。
- 枠サイズは本番と同じ発想: 各枠証拠金=equity/枠数×slot_size_pct、名目=×leverage。
  equityは「確定損益だけ」で更新する(含み益で自己増幅させない=保守的)。
- 手数料: Hyperliquid taker 0.045%(=0.00045)を建て/決済の両方に適用。偽の
  好成績を出さないため、必ず手数料を引く。

これは「過去のこの相場ならこうなっていた」の再現であり、将来を保証しない。
返り値の summary は、この前提のまま正直な数字だけを返す。
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "user_data", "strategies"))
import strategy_core  # noqa: E402  (本番と同じ頭脳)

import hl_loop  # noqa: E402  (fetch_candles, _core_params, _slot_notional, DEFAULT_UNIVERSE)
import hl_schemas  # noqa: E402
import tenant_store  # noqa: E402
import hl_connector  # noqa: E402

TAKER_FEE = float(os.environ.get("HL_BACKTEST_FEE", "0.00045"))  # 0.045% (Hyperliquid taker)
_PER_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000,
           "4h": 14_400_000, "1d": 86_400_000}


def _prep_coin(coin, interval, lookback, p):
    """1銘柄の履歴を取得し、本番と同じ指標・エントリー/決済シグナルを事前計算する。
    date -> (close, entry_long, entry_short, exit_long, exit_short) の辞書群を返す。"""
    df = None
    for attempt in range(3):  # 公開APIのバースト・レート制限で空が返ることがある→リトライ
        df = hl_loop.fetch_candles(coin, interval, lookback)
        if df is not None and not df.empty and len(df) >= 5:
            break
        time.sleep(0.4 * (attempt + 1))
    if df is None or df.empty or len(df) < 5:
        return None
    df = strategy_core.populate_indicators(df, p)
    el = strategy_core.entry_long_cond(df, p)
    es = strategy_core.entry_short_cond(df, p)
    xl = strategy_core.exit_long_cond(df, p)
    xs = strategy_core.exit_short_cond(df, p)
    dates = [int(x) for x in df["date"].tolist()]
    return {
        "close": dict(zip(dates, (float(x) for x in df["close"].tolist()))),
        "entry_long": dict(zip(dates, (bool(x) for x in el.fillna(False).tolist()))),
        "entry_short": dict(zip(dates, (bool(x) for x in es.fillna(False).tolist()))),
        "exit_long": dict(zip(dates, (bool(x) for x in xl.fillna(False).tolist()))),
        "exit_short": dict(zip(dates, (bool(x) for x in xs.fillna(False).tolist()))),
        "dates": dates,
    }


def run_backtest(username=None, params=None, universe=None, interval="1h",
                 days=30, starting_equity=1000.0, coin_cap=50, return_trades=False):
    """本番ループ(hl_engine)を履歴で再生。返り値=正直なサマリ。

    username指定時はそのテナントの現在パラメータで、params指定時はそれで走る
    (「今の設定でバックテスト」と「もし変えたら」の両対応)。
    """
    t_start = time.time()
    if params is None:
        if username:
            p = hl_loop._core_params(username)
        else:
            schema = hl_schemas.SCHEMAS[hl_schemas.DEFAULT_STRATEGY]
            p = {s["key"]: s["default"] for s in schema}
    else:
        p = dict(params)

    universe = (universe or hl_loop.DEFAULT_UNIVERSE)[:coin_cap]
    per_ms = _PER_MS.get(interval, 3_600_000)
    bars_per_day = 86_400_000 / per_ms
    lookback = int(days * bars_per_day) + int(max(int(p.get("ema_slow", 26)),
                                                  int(p.get("box_lookback", 24))) + 30)
    slots = max(1, int(p.get("max_open_trades", hl_schemas.DEFAULT_MAX_OPEN_TRADES)))
    leverage = int(p.get("leverage", 2))
    slot_size_pct = float(p.get("slot_size_pct", 100.0))
    stoploss = float(p.get("stoploss_pct", -6.0)) / 100.0
    trail_trigger = float(p.get("peak_trail_trigger_pct", 4.0)) / 100.0
    trail_giveback = float(p.get("peak_trail_giveback_pct", 25.0)) / 100.0
    allow_long = bool(p.get("is_long_enabled", True))
    allow_short = bool(p.get("is_short_enabled", False))

    # 1) 履歴を集める(銘柄ごと1回・指標事前計算)。取得できた銘柄のみ対象。
    data, all_dates, skipped = {}, set(), []
    for coin in universe:
        try:
            d = _prep_coin(coin, interval, lookback, p)
        except Exception as exc:
            d, _ = None, exc
        if d is None:
            skipped.append(coin)
            continue
        data[coin] = d
        all_dates.update(d["dates"])
    if not data:
        return {"ok": False, "reason": "履歴が取得できませんでした", "skipped": skipped}

    timeline = sorted(all_dates)
    warmup = int(max(int(p.get("ema_slow", 26)), int(p.get("box_lookback", 24))) + 5)
    if len(timeline) <= warmup + 2:
        return {"ok": False, "reason": "履歴が短すぎます(warmup不足)"}

    # 2) 本番ループを1バーずつ再生
    equity = float(starting_equity)          # 確定損益だけで更新する現金equity
    peak_hi = equity                          # equityカーブの最高値(ドローダウン計算)
    max_dd = 0.0
    open_pos = {}                             # coin -> {is_short, entry_px, size, notional, peak}
    trades = []                               # 決済済みトレード
    equity_curve = []

    def _profit_ratio(entry_px, cur_px, is_short):
        r = (cur_px - entry_px) / entry_px if entry_px else 0.0
        return -r if is_short else r

    for t in timeline[warmup:]:
        # (a) 決済管理(hl_engine.manage_position のミラー)
        for coin in list(open_pos.keys()):
            d = data.get(coin)
            if d is None or t not in d["close"]:
                continue
            pos = open_pos[coin]
            cur_px = d["close"][t]
            profit = _profit_ratio(pos["entry_px"], cur_px, pos["is_short"])
            reason = None
            if profit <= stoploss:
                reason = "stop_loss"
            else:
                if profit > pos["peak"]:
                    pos["peak"] = profit
                peak = pos["peak"]
                if (peak >= trail_trigger and peak > 0
                        and (peak - profit) / peak >= trail_giveback):
                    reason = "peak_trail"
                else:
                    exit_hit = (d["exit_short"].get(t, False) if pos["is_short"]
                                else d["exit_long"].get(t, False))
                    if exit_hit:
                        reason = "exit_signal"
            if reason:
                gross = pos["notional"] * profit           # レバ込み(notional=証拠金×レバ)
                fee = (pos["notional"] + pos["size"] * cur_px) * TAKER_FEE
                pnl = gross - fee
                equity += pnl
                trades.append({"coin": coin, "side": "short" if pos["is_short"] else "long",
                               "entry_px": pos["entry_px"], "exit_px": cur_px,
                               "profit_ratio": profit, "pnl_usd": pnl, "reason": reason,
                               "exit_time": t})
                del open_pos[coin]

        # (b) 空き枠エントリー(hl_engine run_tenant のミラー・ユニバース順)
        available = slots - len(open_pos)
        if available > 0:
            slot_margin = equity / slots * slot_size_pct / 100.0
            notional = slot_margin * leverage
            for coin in universe:
                if available <= 0:
                    break
                if coin in open_pos:
                    continue
                d = data.get(coin)
                if d is None or t not in d["close"]:
                    continue
                side = None
                if allow_long and d["entry_long"].get(t, False):
                    side = "long"
                elif allow_short and d["entry_short"].get(t, False):
                    side = "short"
                if not side:
                    continue
                entry_px = d["close"][t]
                if entry_px <= 0 or notional <= 0:
                    continue
                size = notional / entry_px
                open_pos[coin] = {"is_short": side == "short", "entry_px": entry_px,
                                  "size": size, "notional": notional, "peak": 0.0}
                available -= 1

        # (c) equityカーブ / ドローダウン(確定ベース)
        peak_hi = max(peak_hi, equity)
        if peak_hi > 0:
            max_dd = max(max_dd, (peak_hi - equity) / peak_hi)
        equity_curve.append((t, equity))

    # 3) 期末に残った建玉を最終価格で評価決済(総リターンを閉じる)
    last_t = timeline[-1]
    open_marked = 0.0
    for coin, pos in list(open_pos.items()):
        d = data.get(coin)
        px = d["close"].get(last_t) if d else None
        if px is None:
            continue
        profit = _profit_ratio(pos["entry_px"], px, pos["is_short"])
        gross = pos["notional"] * profit
        fee = (pos["notional"] + pos["size"] * px) * TAKER_FEE
        open_marked += gross - fee

    # 4) サマリ(正直な数字だけ)
    closed = trades
    wins = [t for t in closed if t["pnl_usd"] > 0]
    losses = [t for t in closed if t["pnl_usd"] <= 0]
    realized_pnl = sum(t["pnl_usd"] for t in closed)
    final_equity = equity + open_marked
    span_ms = (timeline[-1] - timeline[warmup]) or 1
    span_days = span_ms / 86_400_000
    n = len(closed)
    longs = [t for t in closed if t["side"] == "long"]
    shorts = [t for t in closed if t["side"] == "short"]

    return {
        "ok": True,
        # ロング/ショート別・決済理由別の深掘り分析用(サマリだけでは側別PnLが出せない)
        **({"trades": trades} if return_trades else {}),
        "params_source": ("tenant:" + username) if (username and params is None) else "given/default",
        "interval": interval,
        "requested_days": days,
        "covered_days": round(span_days, 1),
        "coins_used": len(data),
        "coins_skipped": skipped,
        "is_testnet": hl_connector.USE_TESTNET,
        "starting_equity": round(starting_equity, 2),
        "final_equity": round(final_equity, 2),
        "total_return_pct": round((final_equity - starting_equity) / starting_equity * 100.0, 2),
        "realized_pnl_usd": round(realized_pnl, 2),
        "open_at_end": len(open_pos),
        "open_marked_pnl_usd": round(open_marked, 2),
        "closed_trades": n,
        "trades_per_day": round(n / span_days, 2) if span_days else None,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / n * 100.0, 1) if n else None,
        "avg_trade_pnl_usd": round(realized_pnl / n, 2) if n else None,
        "best_trade_usd": round(max((t["pnl_usd"] for t in closed), default=0.0), 2),
        "worst_trade_usd": round(min((t["pnl_usd"] for t in closed), default=0.0), 2),
        "long_trades": len(longs),
        "short_trades": len(shorts),
        "max_drawdown_pct": round(max_dd * 100.0, 2),
        "fee_rate": TAKER_FEE,
        "settings": {"max_open_trades": slots, "leverage": leverage,
                     "slot_size_pct": slot_size_pct, "stoploss_pct": stoploss * 100.0,
                     "is_long_enabled": allow_long, "is_short_enabled": allow_short,
                     "enable_breakout_gate": bool(p.get("enable_breakout_gate", False)),
                     "ema_fast": int(p.get("ema_fast", 12)),
                     "ema_slow": int(p.get("ema_slow", 26))},
        "elapsed_sec": round(time.time() - t_start, 1),
    }


def run_fx_backtest(days=60, params=None, coin_cap=20):
    """FX/商品/指数(builder-dex "xyz")のバックテスト。ユニバースはhl_loop.FX_UNIVERSE、
    パラメータ未指定ならFX専用プロファイル(hl_presets.FX_PRESET_PARAMS)。candlesは
    mainnet直叩き(fetch_candlesがdex銘柄を自動でmainnetから取得)。資金不要・読み取りのみ。"""
    import hl_presets
    p = dict(params) if params else dict(hl_presets.FX_PRESET_PARAMS)
    r = run_backtest(params=p, universe=hl_loop.FX_UNIVERSE, interval="1h",
                     days=days, coin_cap=coin_cap)
    if isinstance(r, dict):
        r["market"] = "fx"
    return r


def summarize_ja(r):
    """バックテスト結果を、Kurageさん風の日本語サマリ文にする(チャット返信用)。
    数字はrの事実のみを使う(捏造しない)。"""
    if not r.get("ok"):
        return "ごめんなさい、バックテストを実行できませんでした(%s)。" % r.get("reason", "原因不明")
    net = "＋" if r["total_return_pct"] >= 0 else ""
    s = r["settings"]
    side = ("両建て(ロング+ショート)" if s["is_long_enabled"] and s["is_short_enabled"]
            else "ロングのみ" if s["is_long_enabled"] else "ショートのみ")
    gate = "ON(回数厳選)" if s["enable_breakout_gate"] else "OFF(回数優先)"
    # FXはbuilder-dexでmainnetの価格のみ(testnetに履歴が無い)。クリプトは接続設定に従う。
    if r.get("market") == "fx":
        net_env = "mainnetのFX/商品/指数"
    else:
        net_env = "testnet" if r["is_testnet"] else "mainnet"
    lines = [
        f"バックテスト結果です({net_env}の実データ・過去{r['covered_days']}日・{r['coins_used']}銘柄・{r['interval']}足)。",
        f"■ 成績: 初期${r['starting_equity']} → 期末${r['final_equity']}(リターン {net}{r['total_return_pct']}%)",
        f"　確定損益 {r['realized_pnl_usd']:+.2f} USDC / 未決済{r['open_at_end']}件の評価 {r['open_marked_pnl_usd']:+.2f}",
        f"■ トレード: {r['closed_trades']}回(1日あたり約{r['trades_per_day']}回) 勝率{r['win_rate_pct']}% "
        f"(勝{r['wins']}/負{r['losses']} ロング{r['long_trades']}/ショート{r['short_trades']})",
        f"　平均{r['avg_trade_pnl_usd']:+.2f} 最大益{r['best_trade_usd']:+.2f} 最大損{r['worst_trade_usd']:+.2f} "
        f"最大ドローダウン{r['max_drawdown_pct']}%",
        f"■ 設定: 枠{s['max_open_trades']} レバ{s['leverage']}倍 {side} ブレイクゲート{gate} "
        f"EMA{s['ema_fast']}/{s['ema_slow']} 手数料{r['fee_rate']*100:.3f}%込み",
        "※過去の再現であって将来の利益を保証するものではありません。",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    import json
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    cap = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    r = run_backtest(days=days, coin_cap=cap)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    print("\n" + summarize_ja(r))
