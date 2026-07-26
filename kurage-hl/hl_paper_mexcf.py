"""MEXC先物ペーパートレード(ショート対応・実弾ゼロ)。

kfreqaiは現物dry-run運用でショートができない。freqtradeはMEXC先物に非対応
(list-exchanges実測)。そこで hl_paper_fx と同じ方式で、**MEXC先物(Contract)の
実価格**を使い、strategy_coreのショート込み戦略をペーパーで回す。

- 価格: kurage-mexcf/mexcf_client.kline (contract.mexc.com 公開API・認証不要)
- 約定: 仮想(実発注ゼロ)。tenant_store.paper_* に market='mexcf' で記録
- 頭脳: strategy_core(共通コア) + kcbrain判断ゲート(adminは無料gemma)
- 実弾化: 将来 kurage-mexcf/mexcf_client の発注(MEXCF_LIVE=1ガード付き)に
  差し替えられる構造。ただし実弾化はユーザーの明示GOがあるまで一切しない。
"""
import os
import sys
import time
import traceback

import pandas as pd

_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_BASE, "..", "user_data", "strategies"))
sys.path.insert(0, os.path.join(_BASE, "..", "kurage-mexcf"))
import strategy_core  # noqa: E402
import mexcf_client  # noqa: E402  (公開kline用。発注はここでは使わない)

import hl_brain_client as brain  # noqa: E402
import hl_schemas  # noqa: E402
import tenant_store  # noqa: E402

MARKET = "mexcf"
ADMIN_USERNAME = os.environ.get("HL_ADMIN_USERNAME", "xb_bittensor")
STARTING_EQUITY = float(os.environ.get("HL_PAPER_START_EQUITY", "1000"))
TAKER_FEE = float(os.environ.get("MEXCF_TAKER_FEE", "0.0002"))  # MEXC先物taker 0.02%
BRAIN_GATE_ENABLED = os.environ.get("HL_BRAIN_GATE", "1") == "1"

# MEXC先物ユニバース(流動性上位・kfreqai現物ペアに概ね対応)。HL_MEXCF_UNIVERSEで上書き可。
DEFAULT_UNIVERSE = [s.strip() for s in os.environ.get(
    "HL_MEXCF_UNIVERSE",
    "BTC_USDT,ETH_USDT,SOL_USDT,XRP_USDT,LTC_USDT,DOGE_USDT,ADA_USDT,AVAX_USDT,"
    "LINK_USDT,DOT_USDT,BNB_USDT,SUI_USDT,APT_USDT,ARB_USDT,OP_USDT,TON_USDT,"
    "TRX_USDT,NEAR_USDT,FIL_USDT,ATOM_USDT"
).split(",") if s.strip()]

_KLINE_CACHE = {}
_KLINE_TTL = 90


def _params():
    """標準クリプトプロファイル(HLスキーマ既定=枠10/レバ2/両建て/ストップ-6/トレール4-25)。"""
    return {s["key"]: s["default"] for s in hl_schemas.SCHEMAS[hl_schemas.DEFAULT_STRATEGY]}


def fetch_candles(symbol, lookback=500):
    """MEXC先物の1時間足をDataFrame(open/high/low/close/volume)で。短命キャッシュ付き。"""
    now = time.time()
    hit = _KLINE_CACHE.get(symbol)
    if hit and now - hit[0] < _KLINE_TTL:
        return hit[1].copy()
    end = int(now)
    start = end - lookback * 3600
    k = mexcf_client.kline(symbol, "Min60", start=start, end=end)
    if not k or not k.get("time"):
        return pd.DataFrame()
    df = pd.DataFrame({
        "date": [int(t) * 1000 for t in k["time"]],
        "open": [float(x) for x in k["open"]],
        "high": [float(x) for x in k["high"]],
        "low": [float(x) for x in k["low"]],
        "close": [float(x) for x in k["close"]],
        "volume": [float(x) for x in k["vol"]],
    })
    _KLINE_CACHE[symbol] = (now, df)
    return df.copy()


def _fetch_cache():
    cache = {}
    for sym in DEFAULT_UNIVERSE:
        try:
            df = fetch_candles(sym)
            if not df.empty:
                cache[sym] = df
        except Exception as exc:
            print("[papermexcf] kline failed %s: %s" % (sym, str(exc)[:80]), flush=True)
    return cache


def _profit_ratio(entry_px, cur_px, is_short):
    if not entry_px:
        return 0.0
    r = (cur_px - entry_px) / entry_px
    return -r if is_short else r


def run_tenant(username, cache, gate=None):
    """1テナント分のペーパーMEXC先物(hl_paper_fx.run_tenantと同型)。"""
    account = tenant_store.paper_get_account(username, MARKET)
    if not account or not account.get("enabled"):
        return {"username": username, "skipped": "paper mexcf disabled"}
    p = _params()
    gate = gate or {}
    slots = max(1, int(p.get("max_open_trades", 10)))
    positions = tenant_store.paper_get_positions(username, MARKET)
    held = {pos["coin"] for pos in positions}
    closed, opened = [], []

    for pos in positions:  # 決済管理
        df = cache.get(pos["coin"])
        if df is None:
            continue
        df = strategy_core.populate_indicators(df.copy(), p)
        coin = pos["coin"]
        is_short = bool(pos["is_short"])
        cur_px = float(df["close"].iloc[-1])
        profit = _profit_ratio(pos["entry_px"], cur_px, is_short)
        reason = None
        if profit <= float(p.get("stoploss_pct", -6.0)) / 100.0:
            reason = "stop_loss"
        else:
            peak = max(float(pos["peak"]), profit)
            if peak != float(pos["peak"]):
                tenant_store.paper_update_position_peak(username, MARKET, coin, peak)
            trig = float(p.get("peak_trail_trigger_pct", 4.0)) / 100.0
            give = float(p.get("peak_trail_giveback_pct", 25.0)) / 100.0
            if peak >= trig and peak > 0 and (peak - profit) / peak >= give:
                reason = "peak_trail"
            else:
                x = (strategy_core.exit_short_cond(df, p) if is_short
                     else strategy_core.exit_long_cond(df, p))
                if bool(x.iloc[-1]):
                    reason = "exit_signal"
        if reason:
            gross = float(pos["notional"]) * profit
            fee = (float(pos["notional"]) + float(pos["size"]) * cur_px) * TAKER_FEE
            pnl = gross - fee
            tenant_store.paper_add_realized(username, MARKET, pnl)
            tenant_store.paper_close_position(username, MARKET, coin)
            tenant_store.paper_add_fill(username, MARKET, coin,
                                        "sell" if not is_short else "buy",
                                        cur_px, pos["size"], pnl, reason, int(time.time() * 1000))
            held.discard(coin)
            closed.append({"coin": coin, "reason": reason, "pnl": round(pnl, 2)})

    account = tenant_store.paper_get_account(username, MARKET)
    equity = float(account["starting_equity"]) + float(account["realized_pnl"])
    available = max(0, slots - len(held))
    for sym in DEFAULT_UNIVERSE:  # 空き枠エントリー(ショート込み・kcbrainゲート)
        if available <= 0:
            break
        if sym in held:
            continue
        df = cache.get(sym)
        if df is None:
            continue
        df = strategy_core.populate_indicators(df.copy(), p)
        d = strategy_core.decide_target_side(
            df, p, allow_long=bool(p.get("is_long_enabled", True)),
            allow_short=bool(p.get("is_short_enabled", True)))
        if not d.get("side"):
            continue
        ok_gate, why = brain.entry_allowed(gate, sym, d["side"])
        if not ok_gate:
            opened.append({"coin": sym, "side": d["side"], "gated": why})
            continue
        price = float(df["close"].iloc[-1])
        slot_margin = equity / slots * float(p.get("slot_size_pct", 100.0)) / 100.0
        notional = slot_margin * int(p.get("leverage", 2))
        size = round(notional / price, 6) if price else 0
        if size <= 0:
            continue
        tenant_store.paper_open_position(username, MARKET, sym, d["side"] == "short",
                                         price, size, notional)
        tenant_store.paper_add_fill(username, MARKET, sym,
                                    "buy" if d["side"] == "long" else "sell",
                                    price, size, 0.0, "open_" + d["side"], int(time.time() * 1000))
        held.add(sym)
        available -= 1
        opened.append({"coin": sym, "side": d["side"], "size": size})
    return {"username": username, "slots": slots, "closed": closed, "opened": opened}


def run_cycle():
    tenants = tenant_store.paper_list_enabled(MARKET)
    if not tenants:
        return {"paper_mexcf_tenants": 0}
    cache = _fetch_cache()
    if not cache:
        return {"paper_mexcf_tenants": len(tenants), "error": "no mexcf klines"}
    gates = {}
    if BRAIN_GATE_ENABLED:
        p = _params()
        assets = []
        for sym, df in cache.items():
            try:
                assets.append(brain.build_asset_evidence(
                    sym.replace("_USDT", ""),
                    strategy_core.populate_indicators(df.copy(), p), "crypto"))
            except Exception:
                continue
        providers = sorted({brain.provider_for(t["username"], ADMIN_USERNAME) for t in tenants})
        for provider in providers:
            try:
                gates[provider] = brain.market_gate("crypto", assets, provider=provider)
            except Exception as exc:
                gates[provider] = {}
                print("[papermexcf] gate failed (%s): %s" % (provider, str(exc)[:100]), flush=True)
    results = []
    for t in tenants:
        try:
            gate = gates.get(brain.provider_for(t["username"], ADMIN_USERNAME)) or {}
            results.append(run_tenant(t["username"], cache, gate))
        except Exception:
            results.append({"username": t["username"], "error": traceback.format_exc()[:200]})
    return {"paper_mexcf_tenants": len(tenants), "mexcf_coins": len(cache), "results": results}


def paper_dashboard(username):
    """ペーパーMEXC先物のサマリ(hl_paper_fx.paper_dashboardと同型・実価格評価)。"""
    import datetime
    account = tenant_store.paper_get_account(username, MARKET)
    if not account:
        return {"enabled": False, "starting_equity": STARTING_EQUITY}
    positions_raw = tenant_store.paper_get_positions(username, MARKET)
    positions, unrealized = [], 0.0
    for pos in positions_raw:
        df = fetch_candles(pos["coin"], 3)
        cur_px = float(df["close"].iloc[-1]) if not df.empty else float(pos["entry_px"])
        profit = _profit_ratio(pos["entry_px"], cur_px, bool(pos["is_short"]))
        upnl = float(pos["notional"]) * profit
        unrealized += upnl
        positions.append({
            "coin": pos["coin"].replace("_USDT", ""), "is_short": bool(pos["is_short"]),
            "size": pos["size"], "entry_px": pos["entry_px"], "cur_px": cur_px,
            "position_value_usd": float(pos["size"]) * cur_px,
            "unrealized_pnl_usd": upnl, "return_on_equity": profit,
            "leverage": int(_params().get("leverage", 2)), "liquidation_px": None,
        })
    realized = float(account["realized_pnl"])
    starting = float(account["starting_equity"])
    fills_raw = tenant_store.paper_recent_fills(username, MARKET, 50)
    fills = [{"coin": f["coin"].replace("_USDT", ""), "side": f["side"], "dir": f["reason"],
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
    shorts = sum(1 for x in positions if x["is_short"])
    return {"enabled": bool(account["enabled"]), "paper": True, "market": MARKET,
            "account_value_usd": starting + realized + unrealized,
            "starting_equity": starting, "unrealized_pnl_usd": unrealized,
            "closed_pnl_total_usd": realized, "positions": positions,
            "short_count": shorts, "long_count": len(positions) - shorts,
            "fills": fills, "daily": daily, "fills_count": len(fills_raw),
            "max_open_trades": int(_params().get("max_open_trades", 10)),
            "universe": [s.replace("_USDT", "") for s in DEFAULT_UNIVERSE]}
