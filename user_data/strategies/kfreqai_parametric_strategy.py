"""Kfreqai Parametric Strategy — a published, self-contained, screen-tunable strategy.

Unlike the private production strategy (kurage_freqai_strategy.py, gitignored),
this one is meant to ship: a user can download the repo and change its behaviour
by editing NUMBERS from the dashboard — no code, no "vibe coding". Every knob is
declared in PARAM_SCHEMA and read at runtime from strategy_params.json via
strategy_params.py (fail-open to the schema defaults). Changing entry LOGIC still
needs code; changing thresholds/sizes/exits does not.

Scope: spot, long-only. Futures/short/grid are deliberately out of scope here.

It also demonstrates two ported nofx ideas as parameter-driven features:
  (1) regime-adaptive position sizing  -> custom_stake_amount
  (2) peak-PnL give-back trailing exit -> custom_exit
"""
from datetime import datetime
from typing import Optional

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy

import strategy_params


class KfreqaiParametricStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "5m"
    can_short = False
    process_only_new_candles = True
    use_exit_signal = True
    use_custom_stoploss = True

    # Class-level attrs are permissive; the real limits come from params.
    minimal_roi = {"0": 100}   # disabled -> exits handled by signals + custom_exit
    stoploss = -0.99           # disabled -> real stop set live in custom_stoploss
    startup_candle_count = 420

    # ------------------------------------------------------------------ schema
    # One generic dashboard form is rendered from this. Only numbers/enums.
    PARAM_SCHEMA = [
        # -- entry --
        {"key": "ema_fast", "type": "int", "default": 12, "min": 2, "max": 100, "step": 1,
         "group": "entry", "label": {"ja": "短期EMA期間", "en": "Fast EMA period"}},
        {"key": "ema_slow", "type": "int", "default": 26, "min": 5, "max": 400, "step": 1,
         "group": "entry", "label": {"ja": "長期EMA期間", "en": "Slow EMA period"}},
        {"key": "rsi_period", "type": "int", "default": 14, "min": 2, "max": 50, "step": 1,
         "group": "entry", "label": {"ja": "RSI期間", "en": "RSI period"}},
        {"key": "rsi_entry_max", "type": "float", "default": 45.0, "min": 0.0, "max": 100.0, "step": 1.0,
         "group": "entry", "label": {"ja": "エントリー上限RSI", "en": "Max RSI to enter"}},
        # -- exit --
        {"key": "rsi_exit_min", "type": "float", "default": 70.0, "min": 0.0, "max": 100.0, "step": 1.0,
         "group": "exit", "label": {"ja": "決済RSI下限", "en": "Min RSI to exit"}},
        {"key": "use_ema_cross_exit", "type": "bool", "default": True,
         "group": "exit", "label": {"ja": "EMAデッドクロスで決済", "en": "Exit on EMA cross-down"}},
        # -- risk --
        {"key": "stoploss_pct", "type": "float", "default": -6.0, "min": -50.0, "max": -0.5, "step": 0.5,
         "group": "risk", "label": {"ja": "ストップロス(%)", "en": "Stop-loss (%)"}},
        # (2) peak-PnL give-back trailing exit
        {"key": "peak_trail_trigger_pct", "type": "float", "default": 5.0, "min": 0.0, "max": 100.0, "step": 0.5,
         "group": "risk", "label": {"ja": "利確トレール発動益(%)", "en": "Peak-trail arm profit (%)"}},
        {"key": "peak_trail_giveback_pct", "type": "float", "default": 40.0, "min": 5.0, "max": 95.0, "step": 5.0,
         "group": "risk", "label": {"ja": "ピークからの押し戻し許容(%)", "en": "Give-back from peak (%)"}},
        # -- sizing: (1) regime-adaptive position sizing (ATR% bands) --
        {"key": "regime_atr_narrow", "type": "float", "default": 1.0, "min": 0.1, "max": 10.0, "step": 0.1,
         "group": "sizing", "label": {"ja": "レジーム閾値: Narrow ATR%", "en": "Regime: Narrow ATR%"}},
        {"key": "regime_atr_standard", "type": "float", "default": 2.0, "min": 0.1, "max": 10.0, "step": 0.1,
         "group": "sizing", "label": {"ja": "レジーム閾値: Standard ATR%", "en": "Regime: Standard ATR%"}},
        {"key": "regime_atr_wide", "type": "float", "default": 3.0, "min": 0.1, "max": 15.0, "step": 0.1,
         "group": "sizing", "label": {"ja": "レジーム閾値: Wide ATR%", "en": "Regime: Wide ATR%"}},
        {"key": "pos_pct_narrow", "type": "float", "default": 40.0, "min": 5.0, "max": 100.0, "step": 5.0,
         "group": "sizing", "label": {"ja": "建玉%: Narrow", "en": "Position %: Narrow"}},
        {"key": "pos_pct_standard", "type": "float", "default": 70.0, "min": 5.0, "max": 100.0, "step": 5.0,
         "group": "sizing", "label": {"ja": "建玉%: Standard(基準)", "en": "Position %: Standard (ref)"}},
        {"key": "pos_pct_wide", "type": "float", "default": 60.0, "min": 5.0, "max": 100.0, "step": 5.0,
         "group": "sizing", "label": {"ja": "建玉%: Wide", "en": "Position %: Wide"}},
        {"key": "pos_pct_volatile", "type": "float", "default": 40.0, "min": 5.0, "max": 100.0, "step": 5.0,
         "group": "sizing", "label": {"ja": "建玉%: Volatile", "en": "Position %: Volatile"}},
    ]

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._p = strategy_params.read_params(self.__class__.__name__, self.PARAM_SCHEMA)
        self._peak: dict = {}  # trade.id -> peak profit ratio seen (feature 2)

    # Re-read the file each loop so dashboard edits take effect without a restart
    # (same live-read discipline as advisory_state.py).
    def _reload_params(self) -> None:
        self._p = strategy_params.read_params(self.__class__.__name__, self.PARAM_SCHEMA)

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        self._reload_params()

    # --------------------------------------------------------------- indicators
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        p = self._p
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=int(p["ema_fast"]))
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=int(p["ema_slow"]))
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=int(p["rsi_period"]))
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        # ATR as % of price drives the volatility regime (feature 1).
        dataframe["atr_pct"] = (dataframe["atr"] / dataframe["close"]) * 100.0
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        p = self._p
        cross_up = (dataframe["ema_fast"] > dataframe["ema_slow"]) & (
            dataframe["ema_fast"].shift(1) <= dataframe["ema_slow"].shift(1)
        )
        dataframe.loc[
            cross_up & (dataframe["rsi"] < float(p["rsi_entry_max"])) & (dataframe["volume"] > 0),
            "enter_long",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        p = self._p
        cond = dataframe["rsi"] > float(p["rsi_exit_min"])
        if bool(p["use_ema_cross_exit"]):
            cross_dn = (dataframe["ema_fast"] < dataframe["ema_slow"]) & (
                dataframe["ema_fast"].shift(1) >= dataframe["ema_slow"].shift(1)
            )
            cond = cond | cross_dn
        dataframe.loc[cond & (dataframe["volume"] > 0), "exit_long"] = 1
        return dataframe

    # ---------------------------------------------------- (1) regime-adaptive size
    def _regime_position_pct(self, pair: str) -> float:
        """Position % for the pair's current volatility regime (ATR% bands)."""
        p = self._p
        try:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            atr_pct = float(df["atr_pct"].iloc[-1])
        except Exception:
            return float(p["pos_pct_standard"])
        if atr_pct < float(p["regime_atr_narrow"]):
            return float(p["pos_pct_narrow"])
        if atr_pct < float(p["regime_atr_standard"]):
            return float(p["pos_pct_standard"])
        if atr_pct < float(p["regime_atr_wide"]):
            return float(p["pos_pct_wide"])
        return float(p["pos_pct_volatile"])

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str], side: str,
                            **kwargs) -> float:
        # Scale the framework's proposed stake by the regime, relative to the
        # "standard" regime (so standard == unchanged, narrow/volatile smaller).
        ref = float(self._p["pos_pct_standard"]) or 70.0
        mult = self._regime_position_pct(pair) / ref
        stake = proposed_stake * mult
        if min_stake is not None:
            stake = max(stake, min_stake)
        return min(stake, max_stake)

    # ---------------------------------------------- (2) peak-PnL give-back exit
    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs) -> Optional[str]:
        p = self._p
        trigger = float(p["peak_trail_trigger_pct"]) / 100.0
        giveback = float(p["peak_trail_giveback_pct"]) / 100.0
        peak = max(self._peak.get(trade.id, 0.0), current_profit)
        self._peak[trade.id] = peak
        # Only trail once the trade has earned enough to protect.
        if peak >= trigger and peak > 0:
            drawdown_from_peak = (peak - current_profit) / peak
            if drawdown_from_peak >= giveback:
                return "peak_trail"
        return None

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                        current_profit: float, **kwargs) -> float:
        # Fixed stop, tunable from the dashboard. Return as a ratio (negative).
        return float(self._p["stoploss_pct"]) / 100.0

    def confirm_trade_exit(self, pair: str, trade: Trade, order_type: str, amount: float,
                           rate: float, time_in_force: str, exit_reason: str,
                           current_time: datetime, **kwargs) -> bool:
        # Clean up the peak cache once a trade actually closes.
        self._peak.pop(trade.id, None)
        return True
