"""Kfreqai Mean-Reversion Strategy — buy oversold pullbacks inside an uptrend.

A different EDGE from the momentum EMA-cross template: instead of chasing
breakouts (which the grid search showed has no profitable parameter set on 5m),
this buys dips (RSI oversold) only while price is above a long trend SMA, and
sells the bounce (RSI recovered) / take-profit / stop. Mean reversion inside an
uptrend is a classic, better-founded edge on crypto.

Spot, long-only. Tunable from the dashboard (schema in param_schemas.py under
"KfreqaiMeanRevStrategy"); read live from strategy_params.json.
"""
from datetime import datetime
from typing import Optional

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy

import strategy_params
from param_schemas import SCHEMAS


class KfreqaiMeanRevStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"   # higher TF cuts fee drag -> this edge is net-positive at 1h, not 5m
    can_short = False
    process_only_new_candles = True
    use_exit_signal = True
    use_custom_stoploss = True

    minimal_roi = {"0": 100}   # take-profit handled in custom_exit (tunable tp_pct)
    stoploss = -0.99           # real stop set live in custom_stoploss
    startup_candle_count = 420

    PARAM_SCHEMA = SCHEMAS["KfreqaiMeanRevStrategy"]

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._p = strategy_params.read_params(self.__class__.__name__, self.PARAM_SCHEMA)
        self._last_exit: dict = {}

    def _reload_params(self) -> None:
        self._p = strategy_params.read_params(self.__class__.__name__, self.PARAM_SCHEMA)

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        self._reload_params()

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        p = self._p
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=int(p["rsi_period"]))
        dataframe["trend_sma"] = ta.SMA(dataframe, timeperiod=int(p["trend_sma"]))
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        p = self._p
        cond = (dataframe["rsi"] < float(p["rsi_buy"])) & (dataframe["volume"] > 0)
        if bool(p["use_trend_filter"]):
            cond = cond & (dataframe["close"] > dataframe["trend_sma"])
        dataframe.loc[cond, "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        p = self._p
        dataframe.loc[
            (dataframe["rsi"] > float(p["rsi_sell"])) & (dataframe["volume"] > 0),
            "exit_long",
        ] = 1
        return dataframe

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs) -> Optional[str]:
        if current_profit >= float(self._p["tp_pct"]) / 100.0:
            return "take_profit"
        return None

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                        current_profit: float, **kwargs) -> float:
        return float(self._p["stoploss_pct"]) / 100.0

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: Optional[str],
                            side: str, **kwargs) -> bool:
        cd = float(self._p["reentry_cooldown_min"])
        if cd <= 0:
            return True
        last = self._last_exit.get(pair)
        return not (last is not None and (current_time - last).total_seconds() / 60.0 < cd)

    def confirm_trade_exit(self, pair: str, trade: Trade, order_type: str, amount: float,
                           rate: float, time_in_force: str, exit_reason: str,
                           current_time: datetime, **kwargs) -> bool:
        reason = (exit_reason or "").lower()
        protected = any(r in reason for r in ("stop", "liquidation", "take_profit", "force", "emergency"))
        if not protected:
            held = (current_time - trade.open_date_utc).total_seconds() / 60.0
            if held < float(self._p["min_hold_min"]):
                return False
        self._last_exit[pair] = current_time
        return True
