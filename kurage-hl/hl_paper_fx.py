"""テナント別ペーパーFX(実弾なしの仮想売買。FXの先行体験)。

資金もウォレット委任も不要。usernameだけで仮想口座($1000スタート等)を持ち、
エンジンが毎時、mainnetの実FX価格で約定をシミュレーションして建玉・損益をDBに
持つ(tenant_store.paper_*)。ロジックは本番と完全共通:
  - 頭脳: strategy_core(EMAクロス+RSI)  ← cryptoと同じ
  - 枠/ストップ/ピークトレール           ← hl_engine と同じ計算
  - kfxbrainのAI判断ゲート               ← admin=無料gemma / 一般=x402 DeepSeek
違いは「Hyperliquidに発注せず自前DBで約定を模す」ことだけ。将来の実弾FX
(perp_dexs発注)に、そのまま差し替えられる構造にしてある。

アンバサダー(一般ユーザー)がXログインだけで始められるよう、agent委任や入金は不要。
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

MARKET = "fx"
INTERVAL = os.environ.get("HL_DEFAULT_INTERVAL", "1h")
ADMIN_USERNAME = os.environ.get("HL_ADMIN_USERNAME", "xb_bittensor")
STARTING_EQUITY = float(os.environ.get("HL_PAPER_FX_START_EQUITY", "2000"))  # kfxaiレーン(¥300,000)相当のドル建て(2026-07-29統一)
TAKER_FEE = float(os.environ.get("HL_BACKTEST_FEE", "0.00045"))
BRAIN_GATE_ENABLED = os.environ.get("HL_BRAIN_GATE", "1") == "1"


def _params():
    """ペーパーFXの戦略パラメータ(FX専用プロファイル)。"""
    return dict(hl_presets.FX_PRESET_PARAMS)


def _profit_ratio(entry_px, cur_px, is_short):
    if not entry_px:
        return 0.0
    r = (cur_px - entry_px) / entry_px
    return -r if is_short else r


def _fetch_fx_cache():
    """FXユニバースの生ローソク足を1回ずつ取得(全テナントで使い回す)。mainnet直叩き。"""
    cache = {}
    for coin in hl_loop.FX_UNIVERSE:
        try:
            df = hl_loop.fetch_candles(coin, INTERVAL, hl_loop.CANDLE_LOOKBACK)
            if not df.empty:
                cache[coin] = df
        except Exception as exc:
            print("[paperfx] candle fetch failed %s: %s" % (coin, str(exc)[:80]), flush=True)
    return cache


def _equity(account):
    """確定ベースの現金equity(枠サイズ計算用。backtestと同じ発想で保守的)。"""
    return float(account["starting_equity"]) + float(account["realized_pnl"])


def _manage(username, pos, df, p):
    """保有ペーパー建玉1件の決済判断。exit理由かNone(hl_engine.manage_positionと同じ)。"""
    coin = pos["coin"]
    is_short = bool(pos["is_short"])
    cur_px = float(df["close"].iloc[-1])
    profit = _profit_ratio(pos["entry_px"], cur_px, is_short)
    if profit <= float(p.get("stoploss_pct", -2.5)) / 100.0:
        return "stop_loss", cur_px, profit
    peak = max(float(pos["peak"]), profit)
    if peak != float(pos["peak"]):
        tenant_store.paper_update_position_peak(username, MARKET, coin, peak)
    trigger = float(p.get("peak_trail_trigger_pct", 1.5)) / 100.0
    giveback = float(p.get("peak_trail_giveback_pct", 30.0)) / 100.0
    if peak >= trigger and peak > 0 and (peak - profit) / peak >= giveback:
        return "peak_trail", cur_px, profit
    exit_cond = (strategy_core.exit_short_cond(df, p) if is_short
                 else strategy_core.exit_long_cond(df, p))
    if bool(exit_cond.iloc[-1]):
        return "exit_signal", cur_px, profit
    return None, cur_px, profit


def run_tenant(username, cache, gate=None):
    """1テナント分のペーパーFX: 決済管理 → 空き枠エントリー(すべて仮想約定)。"""
    account = tenant_store.paper_get_account(username, MARKET)
    if not account or not account.get("enabled"):
        return {"username": username, "skipped": "paper fx disabled"}
    p = _params()
    gate = gate or {}
    slots = max(1, int(p.get("max_open_trades", 8)))
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
            tenant_store.paper_add_fill(username, MARKET, pos["coin"],
                                        "sell" if not pos["is_short"] else "buy",
                                        cur_px, pos["size"], pnl, reason, int(time.time() * 1000))
            held.discard(pos["coin"])
            closed.append({"coin": pos["coin"], "reason": reason, "pnl": round(pnl, 2)})

    # 2) 空き枠エントリー(kfxbrainゲート適用)
    account = tenant_store.paper_get_account(username, MARKET)  # realized更新後を反映
    equity = _equity(account)
    available = max(0, slots - len(held))
    for coin in hl_loop.FX_UNIVERSE:
        if available <= 0:
            break
        if coin in held:
            continue
        df = cache.get(coin)
        if df is None:
            continue
        df = strategy_core.populate_indicators(df.copy(), p)
        d = strategy_core.decide_target_side(
            df, p, allow_long=bool(p.get("is_long_enabled", True)),
            allow_short=bool(p.get("is_short_enabled", True)))
        if not d.get("side"):
            continue
        ok_gate, why = brain.entry_allowed(gate, coin, d["side"])
        if not ok_gate:
            opened.append({"coin": coin, "side": d["side"], "gated": why})
            continue
        price = float(df["close"].iloc[-1])
        slot_margin = equity / slots * float(p.get("slot_size_pct", 100.0)) / 100.0
        notional = slot_margin * int(p.get("leverage", 3))
        size = round(notional / price, 6) if price else 0
        if size <= 0:
            continue
        tenant_store.paper_open_position(username, MARKET, coin, d["side"] == "short",
                                         price, size, notional)
        tenant_store.paper_add_fill(username, MARKET, coin,
                                    "buy" if d["side"] == "long" else "sell",
                                    price, size, 0.0, "open_" + d["side"], int(time.time() * 1000))
        held.add(coin)
        available -= 1
        opened.append({"coin": coin, "side": d["side"], "size": size})

    return {"username": username, "slots": slots, "closed": closed, "opened": opened}


def run_cycle():
    """全ペーパーFXテナントを1サイクル。FXローソク足は1回だけ取得、kfxbrainゲートは
    providerごと(admin=gemma / 一般=deepseek)に1回だけ計算(kfreqai同様fail-open)。"""
    tenants = tenant_store.paper_list_enabled(MARKET)
    if not tenants:
        return {"paper_fx_tenants": 0}
    cache = _fetch_fx_cache()
    if not cache:
        return {"paper_fx_tenants": len(tenants), "error": "no fx candles"}
    gates = {}
    if BRAIN_GATE_ENABLED:
        p = _params()
        assets = []
        for coin, df in cache.items():
            try:
                assets.append(brain.build_asset_evidence(
                    coin, strategy_core.populate_indicators(df.copy(), p), "fx"))
            except Exception:
                continue
        gates = brain.build_tenant_gates("fx", assets, tenants, ADMIN_USERNAME)
    results = []
    for t in tenants:
        try:
            gate = gates.get(t["username"]) or {}
            results.append(run_tenant(t["username"], cache, gate))
        except Exception:
            results.append({"username": t["username"], "error": traceback.format_exc()[:200]})
            print("[paperfx] tenant %s failed: %s" % (t["username"], traceback.format_exc()[:300]),
                  flush=True)
    return {"paper_fx_tenants": len(tenants), "fx_coins": len(cache), "results": results}


def paper_dashboard(username, market=MARKET):
    """ペーパー口座のサマリ(残高/建玉/約定/日次)。実弾のget_dashboard相当を仮想口座から。
    含み損益は保有銘柄の現在価格(mainnet)で評価。未開始ならenabled=False。"""
    import datetime
    account = tenant_store.paper_get_account(username, market)
    if not account:
        return {"enabled": False, "starting_equity": STARTING_EQUITY}
    positions_raw = tenant_store.paper_get_positions(username, market)
    price_cache = {}
    positions, unrealized = [], 0.0
    for pos in positions_raw:
        df = price_cache.get(pos["coin"])
        if df is None:
            df = hl_loop.fetch_candles(pos["coin"], INTERVAL, 3)
            price_cache[pos["coin"]] = df
        cur_px = float(df["close"].iloc[-1]) if not df.empty else float(pos["entry_px"])
        profit = _profit_ratio(pos["entry_px"], cur_px, bool(pos["is_short"]))
        upnl = float(pos["notional"]) * profit
        unrealized += upnl
        positions.append({
            "coin": pos["coin"].split(":", 1)[-1], "is_short": bool(pos["is_short"]),
            "size": pos["size"], "entry_px": pos["entry_px"], "cur_px": cur_px,
            "position_value_usd": float(pos["size"]) * cur_px,
            "unrealized_pnl_usd": upnl, "return_on_equity": profit,
            "leverage": int(_params().get("leverage", 3)), "liquidation_px": None,
        })
    realized = float(account["realized_pnl"])
    starting = float(account["starting_equity"])
    fills_raw = tenant_store.paper_recent_fills(username, market, 50)
    fills = [{"coin": f["coin"].split(":", 1)[-1], "side": f["side"], "dir": f["reason"],
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
        "enabled": bool(account["enabled"]), "paper": True, "market": market,
        "payer_wallet": account.get("payer_wallet"),
        "account_value_usd": starting + realized,  # 統一会計仕様: 残高=初期+確定(含みは別表示)
        "starting_equity": starting, "withdrawable_usd": starting + realized,
        "unrealized_pnl_usd": unrealized, "closed_pnl_total_usd": realized,
        "positions": positions, "fills": fills, "daily": daily,
        "fills_count": len(fills_raw),
        "max_open_trades": int(_params().get("max_open_trades", 8)),
    }
