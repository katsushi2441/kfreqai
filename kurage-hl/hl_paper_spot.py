"""テナント別ペーパー現物(実弾なしの仮想現物ロング)。

kfreqaihl は本来 Hyperliquid perp(レバあり両建て)専用だが、「現物ロングだけやりたい
ユーザー」への選択肢として、また kfreqai(現物)との比較検証のために、現物ロングを
ペーパー(仮想)で提供する(2026-07-28、ユーザー確定仕様 B案)。

hl_paper_fx と同じ「実価格で自前DB約定」パターンだが、現物なので:
  - **ロングのみ**(is_short_enabled=False)。現物は持っていないものを売れない
  - **レバレッジ 1倍**。清算(ロスカット)は無い(価格が0になっても借金にならない)
  - ストップロス/トレールは有効(値動きで降りるのは現物でも同じ)
価格はHyperliquidのperpローソクをそのままDEFAULT_UNIVERSE(kfreqaiと揃えた53銘柄)で
使う。頭脳(strategy_core)・枠・AI判断ゲート(kcbrain)は本番と共通。
"""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "user_data", "strategies"))
import strategy_core  # noqa: E402

import hl_brain_client as brain  # noqa: E402
import hl_loop  # noqa: E402
import hl_presets  # noqa: E402
import tenant_store  # noqa: E402

MARKET = "spot"
INTERVAL = os.environ.get("HL_DEFAULT_INTERVAL", "1h")
ADMIN_USERNAME = os.environ.get("HL_ADMIN_USERNAME", "xb_bittensor")
STARTING_EQUITY = float(os.environ.get("HL_PAPER_SPOT_START_EQUITY", "1000"))  # kfreqai現物(10,000)の1/10スケール(2026-07-29ユーザー確定。testnet先物が~2,000のため絶対額統一は不可能、スケール比で比較する)
TAKER_FEE = float(os.environ.get("HL_BACKTEST_FEE", "0.00045"))
BRAIN_GATE_ENABLED = os.environ.get("HL_BRAIN_GATE", "1") == "1"


def _params():
    """現物ロング用パラメータ: cryptoの標準プリセットをベースに、ショート無効・レバ1倍。"""
    p = dict(hl_presets.PRESETS[1]["params"])  # 標準型
    p["is_long_enabled"] = True
    p["is_short_enabled"] = False   # 現物はロングのみ
    p["leverage"] = 1               # 現物=レバなし
    return p


def _profit_ratio(entry_px, cur_px):
    if not entry_px:
        return 0.0
    return (cur_px - entry_px) / entry_px  # 現物ロングのみ


def _fetch_cache():
    """crypto現物ユニバース(kfreqaiと揃えた53銘柄)のローソクを1回ずつ取得。"""
    cache = {}
    for coin in hl_loop.DEFAULT_UNIVERSE:
        try:
            df = hl_loop.fetch_candles(coin, INTERVAL, hl_loop.CANDLE_LOOKBACK)
            if not df.empty:
                cache[coin] = df
        except Exception as exc:
            print("[paperspot] candle fetch failed %s: %s" % (coin, str(exc)[:80]), flush=True)
    return cache


def _equity(account):
    return float(account["starting_equity"]) + float(account["realized_pnl"])


def _manage(username, pos, df, p):
    """保有中の現物ロングの決済判断(ストップ/ピークトレール/EMA反転)。清算は無い。"""
    cur_px = float(df["close"].iloc[-1])
    profit = _profit_ratio(pos["entry_px"], cur_px)
    stoploss = float(p.get("stoploss_pct", -6.0)) / 100.0
    trig = float(p.get("peak_trail_trigger_pct", 4.0)) / 100.0
    give = float(p.get("peak_trail_giveback_pct", 25.0)) / 100.0
    peak = max(float(pos.get("peak", 0.0)), profit)
    if peak > float(pos.get("peak", 0.0)):
        tenant_store.paper_update_position_peak(username, MARKET, pos["coin"], peak)
    if profit <= stoploss:
        return "stop_loss", cur_px, profit
    if peak >= trig and peak > 0 and (peak - profit) / peak >= give:
        return "peak_trail", cur_px, profit
    # exit_long_cond はSeriesを返す。直接 if に渡すと
    # ValueError: The truth value of a Series is ambiguous になり、
    # 決済管理が毎回例外で落ちていた(2026-08-02から24時間で42回)。
    # hl_paper_fx / hl_engine と同じく最終足を取り出して真偽にする。
    if bool(strategy_core.exit_long_cond(df, p).iloc[-1]):
        return "exit_signal", cur_px, profit
    return None, cur_px, profit


def run_tenant(username, cache, gate=None):
    """1テナント分のペーパー現物: 決済管理 → 空き枠エントリー(すべて仮想約定・ロングのみ)。"""
    account = tenant_store.paper_get_account(username, MARKET)
    if not account or not account.get("enabled"):
        return {"username": username, "skipped": "paper spot disabled"}
    p = _params()
    gate = gate or {}
    slots = max(1, int(p.get("max_open_trades", 10)))
    positions = tenant_store.paper_get_positions(username, MARKET)
    held = {pos["coin"] for pos in positions}
    closed, opened = [], []

    # 1) 決済管理
    for pos in positions:
        df = cache.get(pos["coin"])
        if df is None:
            continue
        df = strategy_core.populate_indicators(df.copy(), p)
        reason, cur_px, profit = _manage(username, pos, df, p)
        if reason:
            gross = float(pos["notional"]) * profit
            fee = (float(pos["notional"]) + float(pos["size"]) * cur_px) * TAKER_FEE
            pnl = gross - fee
            tenant_store.paper_add_realized(username, MARKET, pnl)
            tenant_store.paper_close_position(username, MARKET, pos["coin"])
            tenant_store.paper_add_fill(username, MARKET, pos["coin"], "sell",
                                        cur_px, pos["size"], pnl, reason, int(time.time() * 1000))
            held.discard(pos["coin"])
            closed.append({"coin": pos["coin"], "reason": reason, "pnl": round(pnl, 2)})

    # 2) 空き枠エントリー(ロングのみ・kcbrainゲート適用)
    account = tenant_store.paper_get_account(username, MARKET)
    equity = _equity(account)
    available = max(0, slots - len(held))
    for coin in hl_loop.DEFAULT_UNIVERSE:
        if available <= 0:
            break
        if coin in held:
            continue
        df = cache.get(coin)
        if df is None:
            continue
        df = strategy_core.populate_indicators(df.copy(), p)
        # エントリー根拠は hl_engine.decide_entry に集約(HL_ENTRY_SOURCEで切替)。
        # 現物はロング限定なので、freqai予測(long_ok)とそのまま噛み合う。
        import hl_engine
        d = hl_engine.decide_entry(coin, df, dict(p, is_long_enabled=True, is_short_enabled=False))
        if d.get("side") != "long":
            continue
        ok_gate, why = brain.entry_allowed(gate, coin, "long")
        if not ok_gate:
            opened.append({"coin": coin, "side": "long", "gated": why})
            continue
        # FreqAI(非公開モデル)の予測ゲート: 下落見込み(s_close<=0)ならロングを見送る。
        # kfreqaiが下落局面でロングを避ける賢さを、モデルを公開せずkfreqaihlに効かせる。
        if not brain.freqai_long_ok(coin):
            opened.append({"coin": coin, "side": "long", "gated": "freqai:予測が上昇でない"})
            continue
        price = float(df["close"].iloc[-1])
        slot_margin = equity / slots * float(p.get("slot_size_pct", 100.0)) / 100.0
        notional = slot_margin  # レバ1倍(現物): 名目=証拠金
        size = round(notional / price, 6) if price else 0
        if size <= 0:
            continue
        tenant_store.paper_open_position(username, MARKET, coin, False, price, size, notional)
        tenant_store.paper_add_fill(username, MARKET, coin, "buy", price, size, 0.0,
                                    "open_long", int(time.time() * 1000))
        held.add(coin)
        available -= 1
        opened.append({"coin": coin, "side": "long", "size": size})

    return {"username": username, "slots": slots, "closed": closed, "opened": opened}


def run_cycle():
    """全ペーパー現物テナントを1サイクル。ローソクは1回だけ取得、kcbrainゲートは
    providerごと(admin=gemma / 一般=deepseek)に1回だけ計算(kfreqai同様fail-open)。"""
    tenants = tenant_store.paper_list_enabled(MARKET)
    if not tenants:
        return {"paper_spot_tenants": 0}
    cache = _fetch_cache()
    if not cache:
        return {"paper_spot_tenants": len(tenants), "error": "no candles"}
    gates = {}
    if BRAIN_GATE_ENABLED:
        p = _params()
        assets = []
        for coin, df in cache.items():
            try:
                assets.append(brain.build_asset_evidence(
                    coin, strategy_core.populate_indicators(df.copy(), p), "crypto"))
            except Exception:
                continue
        gates = brain.build_tenant_gates("crypto", assets, tenants, ADMIN_USERNAME)
    results = []
    for t in tenants:
        try:
            g = gates.get(t["username"], {})
            results.append(run_tenant(t["username"], cache, g))
        except Exception:
            print("[paperspot] tenant %s failed: %s"
                  % (t["username"], traceback.format_exc()[:300]), flush=True)
    return {"paper_spot_tenants": len(tenants), "spot_coins": len(cache), "results": results}


def paper_dashboard(username):
    """ダッシュボード表示用: 現物ペーパーの口座・保有・約定・日次(hl_paper_fxと同形・現物版)。"""
    import datetime
    account = tenant_store.paper_get_account(username, MARKET)
    if not account:
        return {"enabled": False, "starting_equity": STARTING_EQUITY}
    positions_raw = tenant_store.paper_get_positions(username, MARKET)
    price_cache = {}
    positions, unrealized = [], 0.0
    for pos in positions_raw:
        df = price_cache.get(pos["coin"])
        if df is None:
            df = hl_loop.fetch_candles(pos["coin"], INTERVAL, 3)
            price_cache[pos["coin"]] = df
        cur_px = float(df["close"].iloc[-1]) if not df.empty else float(pos["entry_px"])
        profit = _profit_ratio(pos["entry_px"], cur_px)
        upnl = float(pos["notional"]) * profit
        unrealized += upnl
        positions.append({
            "coin": pos["coin"], "is_short": False,
            "size": pos["size"], "entry_px": pos["entry_px"], "cur_px": cur_px,
            "position_value_usd": float(pos["size"]) * cur_px,
            "unrealized_pnl_usd": upnl, "return_on_equity": profit,
            "leverage": 1, "liquidation_px": None,  # 現物=レバ1倍・清算なし
        })
    realized = float(account["realized_pnl"])
    starting = float(account["starting_equity"])
    fills_raw = tenant_store.paper_recent_fills(username, MARKET, 50)
    fills = [{"coin": f["coin"], "side": f["side"], "dir": f["reason"],
              "px": f["px"], "sz": f["size"], "closed_pnl_usd": f["pnl_usd"], "fee_usd": 0.0,
              "time_ms": f["ts"]} for f in fills_raw]
    jst = datetime.timezone(datetime.timedelta(hours=9))
    byday = {}
    for f in fills_raw:
        d = datetime.datetime.fromtimestamp(f["ts"] / 1000, jst).date().isoformat()
        e = byday.setdefault(d, {"date": d, "abs_profit": 0.0, "trade_count": 0})
        e["abs_profit"] += float(f["pnl_usd"])
        e["trade_count"] += 1
    daily = sorted(byday.values(), key=lambda x: x["date"], reverse=True)[:7]
    return {
        "enabled": bool(account["enabled"]), "paper": True, "market": MARKET,
        "payer_wallet": account.get("payer_wallet"),
        "account_value_usd": starting + realized,  # 統一会計仕様: 残高=初期+確定(含みは別表示)
        "starting_equity": starting, "withdrawable_usd": starting + realized,
        "unrealized_pnl_usd": unrealized, "closed_pnl_total_usd": realized,
        "positions": positions, "fills": fills, "daily": daily,
        "fills_count": len(fills_raw),
        "max_open_trades": int(_params().get("max_open_trades", 10)),
    }
