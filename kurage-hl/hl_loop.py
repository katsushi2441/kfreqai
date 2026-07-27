"""Hyperliquid execution layer — turns the SHARED edge (strategy_core) into orders.

This is the "手足" (hands/feet). The "頭脳" (brain: indicators + entry/exit logic)
is user_data/strategies/strategy_core.py, the SAME module the freqtrade strategy
uses — so kfreqai and kfreqaihl share one definition of the edge (方式: 共通コア).

What differs from kfreqai is only THIS translation layer: freqtrade owns Trade
objects and places spot orders; here we read a tenant's Hyperliquid account and
place perp orders via the SDK. Candles are PUBLIC (no funds needed), so signals
are computed on real market data even in mock mode — only order placement is
simulated when HL_MOCK=1.

Per-tenant position state (stops / peak-trail) is intentionally NOT built yet:
this module currently exposes the fresh-entry decision (decide) and a mock-safe
executor stub. The always-on scheduling loop + stop/trail management is the next
step; kept separate so the shared-core wiring can be verified first.
"""
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "user_data", "strategies"))
import strategy_core  # noqa: E402  (the shared edge, also used by freqtrade)

import hl_connector  # noqa: E402
import hl_schemas  # noqa: E402
import tenant_store  # noqa: E402

DEFAULT_COIN = os.environ.get("HL_DEFAULT_COIN", "ETH")
DEFAULT_INTERVAL = os.environ.get("HL_DEFAULT_INTERVAL", "1h")
CANDLE_LOOKBACK = int(os.environ.get("HL_CANDLE_LOOKBACK", "500"))

# 取引ユニバース(枠を埋める候補銘柄)。kfreqaiのpair_whitelistに相当。
# 頻度の逆算(2026-07-25実測): ゲートOFF+両建てで1銘柄0.583回/日 → 50銘柄で約30回/日。
# ユニバースはkfreqai(検証場)と揃える(2026-07-28刷新)。
# 「kfreqaiでバックテスト→本番→kfreqaihlに実装」の流れのため、両者の銘柄を一致させる。
# 内訳: BTC/ETH(kfreqaiでは低ボラで外しているが選択肢として先頭に持つ)+
#   kfreqai本番whitelist ∩ Hyperliquid **testnet** perp実在 の計53銘柄(testnet約定不可のZEC/CHZは除外)。
# testnet基準にしているのは、検証(dry-run/testnet)で実際に約定シミュレーションできる
# 銘柄に限るため。testnetに無いがmainnetにはある銘柄(XRP/LTC/LINK/UNI/BCH/DOT/ETHFI等)は、
# 実弾のmainnet移行時に環境変数HL_UNIVERSEで広げる。旧50銘柄のMATIC/RNDR/FTM/kPEPE等
# (Hyperliquid上で廃止/改名)は除外済み。
DEFAULT_UNIVERSE = [s.strip() for s in os.environ.get(
    "HL_UNIVERSE",
    "BTC,ETH,SOL,ATOM,XMR,ADA,NEAR,DOGE,HYPE,TAO,SUI,XLM,INJ,TRUMP,TIA,FIL,GRAM,"
    "AVAX,APE,PENDLE,XPL,ARB,CC,DASH,PYTH,ZRO,ICP,AAVE,FET,WLD,CAKE,VINE,LIT,EIGEN,"
    "GALA,KAS,WIF,MINA,RUNE,LDO,PUMP,IO,ALGO,HBAR,VIRTUAL,POL,PENGU,ONDO,GRASS,"
    "DYDX,W,FARTCOIN,KAITO"
).split(",") if s.strip()]

# FX/商品/指数ユニバース(builder-dex "xyz")。mainnetにしか価格履歴が無い(2026-07-26実測)。
# FXは低ボラなのでクリプトとは別枠・別パラメータで扱う(hl_presets.FX_PRESETS)。
# 全てmainnetで30日フル取得できることを確認済み(EUR/JPY/GOLD/SILVER/BRENTOIL/SP500等)。
FX_UNIVERSE = [s.strip() for s in os.environ.get(
    "HL_FX_UNIVERSE",
    "xyz:EUR,xyz:JPY,xyz:GOLD,xyz:SILVER,xyz:BRENTOIL,xyz:COPPER,xyz:PLATINUM,"
    "xyz:PALLADIUM,xyz:NATGAS,xyz:CORN,xyz:WHEAT,xyz:SP500,xyz:JP225,xyz:KR200"
).split(",") if s.strip()]


# fetch_candlesの短命キャッシュ。バックテストのパラメータスイープや連続実行で
# 同じ銘柄を何度も取りに行くと公開APIのレート制限で空が返るため、少しの間だけ
# 結果を使い回す。ローソク足は1時間足なので数十秒のTTLは実運用でも影響しない。
_CANDLE_CACHE = {}
_CANDLE_TTL = int(os.environ.get("HL_CANDLE_CACHE_TTL", "90"))


def fetch_candles(coin=DEFAULT_COIN, interval=DEFAULT_INTERVAL, lookback=CANDLE_LOOKBACK):
    """Public OHLCV from Hyperliquid as a DataFrame (open/high/low/close/volume).
    No auth, no funds needed — candles are public.

    通常のクリプト銘柄(BTC等)は設定中のネットワークから取得。builder-dex銘柄
    (FX/商品/指数、名前が "xyz:EUR" のようにコロン付き)はmainnetにしか価格履歴が
    無く、SDKの candles_snapshot は内部の name_to_coin にbuilder-dex名を持たないため
    KeyErrorになる。そのため dex銘柄は mainnet の /info を raw で直叩きする(実測済み)。"""
    now = time.time()
    ck = (coin, interval, int(lookback))
    hit = _CANDLE_CACHE.get(ck)
    if hit and (now - hit[0]) < _CANDLE_TTL:
        return hit[1].copy()
    per_ms = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000,
              "4h": 14_400_000, "1d": 86_400_000}.get(interval, 3_600_000)
    end = int(now * 1000)
    start = end - lookback * per_ms
    is_dex = ":" in coin  # 例: "xyz:EUR" = builder-deployed perp (FX/商品/株/指数)

    def _fetch_once():
        if is_dex:
            info = hl_connector.mainnet_info_client()
            return info.post("/info", {"type": "candleSnapshot", "req": {
                "coin": coin, "interval": interval, "startTime": start, "endTime": end}})
        info = hl_connector.info_client()
        return info.candles_snapshot(coin, interval, start, end)

    # 多銘柄を1サイクルで連続取得するとHyperliquid公開APIの429に当たる。
    # 429は一時的なので短いバックオフでリトライする(2026-07-28)。
    snap = None
    for attempt in range(3):
        try:
            snap = _fetch_once()
            break
        except Exception as exc:
            if "429" in str(exc) and attempt < 2:
                time.sleep(0.6 * (attempt + 1))
                continue
            raise
    if not snap:
        return pd.DataFrame()
    df = pd.DataFrame([{
        "date": int(c["t"]), "open": float(c["o"]), "high": float(c["h"]),
        "low": float(c["l"]), "close": float(c["c"]), "volume": float(c["v"]),
    } for c in snap])
    _CANDLE_CACHE[ck] = (now, df)
    return df.copy()


def _core_params(username):
    """Effective params for a tenant, filled with schema defaults (same shape the
    dashboard/chat writes). strategy_core reads what it needs and defaults the rest."""
    schema = hl_schemas.SCHEMAS[hl_schemas.DEFAULT_STRATEGY]
    stored = tenant_store.get_params(username)
    p = {}
    for spec in schema:
        p[spec["key"]] = stored.get(spec["key"], spec["default"])
    return p


def decide(username, coin=DEFAULT_COIN, interval=DEFAULT_INTERVAL):
    """Fresh-entry decision for a tenant on one coin, using the shared edge on
    real Hyperliquid candles. Returns a dict (side/reason/context) — does NOT
    place any order. This is the piece that proves kfreqai and kfreqaihl share
    the same brain."""
    p = _core_params(username)
    df = fetch_candles(coin, interval)
    if df.empty:
        return {"coin": coin, "side": None, "reason": "no candles"}
    df = strategy_core.populate_indicators(df, p)
    d = strategy_core.decide_target_side(
        df, p,
        allow_long=bool(p.get("is_long_enabled", True)),
        allow_short=bool(p.get("is_short_enabled", False)))
    last = df.iloc[-1]
    d.update({
        "coin": coin, "interval": interval,
        "price": float(last["close"]),
        "rsi": round(float(last["rsi"]), 1) if pd.notna(last["rsi"]) else None,
        "leverage": int(p.get("leverage", 2)),
        "position_pct_of_equity": float(p.get("position_pct_of_equity", 30.0)),
    })
    return d


def _slot_notional(equity, params):
    """1枠あたりの名目金額。kfreqai本番と同じ発想: 証拠金=残高/枠数、名目=×レバ。
    slot_size_pctで枠あたりを縮小できる(custom_stake_amountのレジーム縮小の簡易版)。"""
    slots = max(1, int(params.get("max_open_trades", hl_schemas.DEFAULT_MAX_OPEN_TRADES)))
    slot_margin = equity / slots * float(params.get("slot_size_pct", 100.0)) / 100.0
    return slot_margin * int(params.get("leverage", 2))


def portfolio_step(username, universe=None, interval=DEFAULT_INTERVAL):
    """本番ループ1回分: 空き枠の数だけ、ユニバースの中でシグナルが出た銘柄に
    エントリーする(kfreqaiのmax_open_trades運用のミラー)。保有中決済(ストップ/
    トレール)はまだ別途(次ステップ)。モック安全。"""
    tenant = tenant_store.get_or_create(username, hl_connector.generate_agent_wallet)
    if not tenant.get("main_wallet_address") or not tenant.get("agent_approved"):
        return {"ok": False, "reason": "agent not approved / no main wallet"}
    universe = universe or DEFAULT_UNIVERSE
    p = _core_params(username)
    slots = max(1, int(p.get("max_open_trades", hl_schemas.DEFAULT_MAX_OPEN_TRADES)))
    dash = hl_connector.get_dashboard(tenant["main_wallet_address"])
    held = {pos["coin"] for pos in (dash.get("positions") or [])}
    open_count = len(held)
    available = max(0, slots - open_count)
    equity = float(dash.get("account_value_usd") or 0)
    actions = []
    for coin in universe:
        if available <= 0:
            break
        if coin in held:
            continue  # 既に枠を使っている銘柄はスキップ
        d = decide(username, coin, interval)
        if not d.get("side"):
            continue
        notional = _slot_notional(equity, p)
        size = round(notional / d["price"], 4) if d.get("price") else 0
        if size <= 0:
            actions.append({"coin": coin, "skipped": "size<=0 (fund account)"})
            continue
        try:
            res = hl_connector.place_order(
                tenant["agent_private_key"], tenant["main_wallet_address"],
                coin, d["side"] == "long", size, leverage=int(p.get("leverage", 2)))
        except NotImplementedError as exc:
            actions.append({"coin": coin, "skipped": str(exc)})
            continue
        actions.append({"coin": coin, "side": d["side"], "size": size, "order_result": res})
        available -= 1
    return {"ok": True, "slots": slots, "open_before": open_count,
            "filled": len([a for a in actions if a.get("side")]),
            "available_after": available, "actions": actions}


def execute(username, coin=DEFAULT_COIN, interval=DEFAULT_INTERVAL):
    """単一銘柄の手動発注(ダッシュボードの「この判断で発注」ボタン用)。枠の空き
    を1つ使う。モック安全。ポートフォリオ全体はportfolio_step。"""
    tenant = tenant_store.get_or_create(username, hl_connector.generate_agent_wallet)
    if not tenant.get("main_wallet_address") or not tenant.get("agent_approved"):
        return {"ok": False, "reason": "agent not approved / no main wallet"}
    p = _core_params(username)
    d = decide(username, coin, interval)
    if not d.get("side"):
        return {"ok": True, "action": "hold", "decision": d}
    dash = hl_connector.get_dashboard(tenant["main_wallet_address"])
    held = {pos["coin"] for pos in (dash.get("positions") or [])}
    slots = max(1, int(p.get("max_open_trades", hl_schemas.DEFAULT_MAX_OPEN_TRADES)))
    if coin not in held and len(held) >= slots:
        return {"ok": False, "reason": "全枠(%d)使用中" % slots, "decision": d}
    equity = float(dash.get("account_value_usd") or 0)
    notional = _slot_notional(equity, p)
    size = round(notional / d["price"], 4) if d["price"] else 0
    if size <= 0:
        return {"ok": False, "reason": "computed size <= 0 (fund the account)", "decision": d}
    try:
        res = hl_connector.place_order(
            tenant["agent_private_key"], tenant["main_wallet_address"],
            d["coin"], d["side"] == "long", size, leverage=int(p.get("leverage", 2)))
    except NotImplementedError as exc:
        return {"ok": False, "reason": str(exc), "decision": d}
    return {"ok": True, "action": "enter_" + d["side"], "size": size,
            "order_result": res, "decision": d}
