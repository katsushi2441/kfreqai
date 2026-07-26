"""Shared signal core — the single source of truth for the EDGE.

Both runtimes import THIS module and call the SAME functions, so the entry/exit
logic lives in exactly one place:
  - freqtrade side (in-container): kfreqai_parametric_strategy.py delegates its
    populate_indicators / populate_entry_trend / populate_exit_trend here.
  - Hyperliquid side (on host): kurage-hl/hl_loop.py feeds Hyperliquid candles
    into the same functions to decide a target position, then translates to orders.

Dependency-light on purpose (same discipline as param_schemas.py): it needs only
pandas, and uses talib IF it happens to be importable. That keeps numbers
identical inside the freqtrade container (talib present -> same as before this
refactor, no backtest regression) while still importable on the host (talib
absent -> a Wilder-smoothed pandas fallback that closely matches talib).

Spot (freqtrade) is long-only, so it uses only the *_long helpers. Perps
(Hyperliquid) can also go short, so the short mirror (*_short) is provided here
too; the caller decides which sides to enable.

`p` everywhere is a plain params dict (the effective values from a schema:
param_schemas on the freqtrade side, hl_schemas on the HL side). Missing keys
fall back to _DEFAULTS so either schema — even a smaller one — can drive it.
"""
import pandas as pd

try:
    import talib.abstract as ta
    _HAS_TALIB = True
except Exception:  # host (HL loop) has no talib -> pandas fallback below
    _HAS_TALIB = False

# Keys the core reads. A schema that omits some (e.g. the simpler HL schema)
# still works: the missing knob takes its default here.
_DEFAULTS = {
    "ema_fast": 12, "ema_slow": 26, "rsi_period": 14,
    "rsi_entry_max": 60.0, "rsi_exit_min": 82.0, "use_ema_cross_exit": False,
    "box_lookback": 24, "breakout_confirm_candles": 1, "enable_breakout_gate": True,
}


def _g(p, key):
    v = (p or {}).get(key)
    return _DEFAULTS[key] if v is None else v


# --------------------------------------------------------------------- indicators
# talib path is byte-for-byte what the strategy used before; pandas path is a
# Wilder-smoothed equivalent so the host gets ~the same values without talib.
def _ema(df, period):
    if _HAS_TALIB:
        return ta.EMA(df, timeperiod=int(period))
    return df["close"].ewm(span=int(period), adjust=False, min_periods=int(period)).mean()


def _rsi(df, period):
    if _HAS_TALIB:
        return ta.RSI(df, timeperiod=int(period))
    period = int(period)
    delta = df["close"].diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(df, period=14):
    if _HAS_TALIB:
        return ta.ATR(df, timeperiod=int(period))
    period = int(period)
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def populate_indicators(df, p):
    """Add every indicator column the entry/exit helpers need. Mirrors the
    original KfreqaiParametricStrategy.populate_indicators, plus the short-side
    breakdown columns (harmless/unused on the spot long-only side)."""
    df["ema_fast"] = _ema(df, _g(p, "ema_fast"))
    df["ema_slow"] = _ema(df, _g(p, "ema_slow"))
    df["rsi"] = _rsi(df, _g(p, "rsi_period"))
    df["atr"] = _atr(df, 14)
    df["atr_pct"] = (df["atr"] / df["close"]) * 100.0
    lookback = max(2, int(_g(p, "box_lookback")))
    confirm = max(1, int(_g(p, "breakout_confirm_candles")))
    df["box_high"] = df["high"].rolling(lookback).max().shift(1)
    broke_up = (df["close"] > df["box_high"]).astype(int)
    df["breakout_confirmed"] = (broke_up.rolling(confirm).sum() >= confirm).astype(int)
    # short mirror (perps only)
    df["box_low"] = df["low"].rolling(lookback).min().shift(1)
    broke_dn = (df["close"] < df["box_low"]).astype(int)
    df["breakdown_confirmed"] = (broke_dn.rolling(confirm).sum() >= confirm).astype(int)
    return df


# ------------------------------------------------------------------ entry / exit
def entry_long_cond(df, p):
    """Boolean Series: EMA cross-up + RSI not overbought (+ optional breakout gate)."""
    cross_up = (df["ema_fast"] > df["ema_slow"]) & (
        df["ema_fast"].shift(1) <= df["ema_slow"].shift(1))
    cond = cross_up & (df["rsi"] < float(_g(p, "rsi_entry_max"))) & (df["volume"] > 0)
    if bool(_g(p, "enable_breakout_gate")):
        cond = cond & (df["breakout_confirmed"] == 1)
    return cond


def exit_long_cond(df, p):
    cond = df["rsi"] > float(_g(p, "rsi_exit_min"))
    if bool(_g(p, "use_ema_cross_exit")):
        cross_dn = (df["ema_fast"] < df["ema_slow"]) & (
            df["ema_fast"].shift(1) >= df["ema_slow"].shift(1))
        cond = cond | cross_dn
    return cond & (df["volume"] > 0)


def entry_short_cond(df, p):
    """Perp-only mirror of entry_long_cond: EMA cross-down + RSI not oversold
    (+ optional breakdown gate). rsi_entry_max is mirrored around 100."""
    cross_dn = (df["ema_fast"] < df["ema_slow"]) & (
        df["ema_fast"].shift(1) >= df["ema_slow"].shift(1))
    cond = cross_dn & (df["rsi"] > (100.0 - float(_g(p, "rsi_entry_max")))) & (df["volume"] > 0)
    if bool(_g(p, "enable_breakout_gate")):
        cond = cond & (df["breakdown_confirmed"] == 1)
    return cond


def exit_short_cond(df, p):
    cond = df["rsi"] < (100.0 - float(_g(p, "rsi_exit_min")))
    if bool(_g(p, "use_ema_cross_exit")):
        cross_up = (df["ema_fast"] > df["ema_slow"]) & (
            df["ema_fast"].shift(1) <= df["ema_slow"].shift(1))
        cond = cond | cross_up
    return cond & (df["volume"] > 0)


# ------------------------------------------------------------- HL convenience
def decide_target_side(df, p, allow_long=True, allow_short=False):
    """For the HL loop: look only at the LAST closed candle and return the
    desired action for a flat account: 'long', 'short', or None (no new entry).
    Position management (stops/trailing/exits) is handled by the caller using the
    *_cond helpers on the open position; this is just the fresh-entry decision.

    Returns a dict so the caller can log why (mirrors advisory_state's note style).
    """
    if df is None or len(df) < 2:
        return {"side": None, "reason": "insufficient candles"}
    last = df.iloc[-1:]
    if allow_long and bool(entry_long_cond(df, p).iloc[-1]):
        return {"side": "long", "reason": "ema_cross_up+breakout"}
    if allow_short and bool(entry_short_cond(df, p).iloc[-1]):
        return {"side": "short", "reason": "ema_cross_down+breakdown"}
    return {"side": None, "reason": "no entry signal on last candle"}
