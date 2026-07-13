"""Default strategy for a fresh clone of this repo.

This is a placeholder, not kfreqai's real strategy. The actual live logic
(entry/exit rules, risk parameters, ROI/stoploss values) is not published
here -- see README. This class exists only so `docker compose up -d`
works out of the box on a clone instead of failing with "strategy class
not found". It never opens a trade.

Production here overrides this via docker-compose.override.yml (gitignored,
see docker-compose.override.yml.example), which is Docker Compose's
standard mechanism for a local-only override on top of the committed
docker-compose.yml -- so this default never affects what's actually running.
"""
from pandas import DataFrame

from freqtrade.strategy import IStrategy


class KfreqaiDefaultStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "5m"
    minimal_roi = {"0": 100}  # effectively disabled
    stoploss = -0.99          # effectively disabled
    process_only_new_candles = True
    use_exit_signal = True

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        return dataframe
