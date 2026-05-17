"""Pair Information filter"""

import logging

from freqtrade.exchange.exchange_types import Tickers
from freqtrade.misc import safe_value_nested
from freqtrade.plugins.pairlist.IPairList import IPairList, PairlistParameter, SupportsBacktesting
from freqtrade.util import FtTTLCache


logger = logging.getLogger(__name__)


class PairInformationFilter(IPairList):
    is_pairlist_generator = True
    supports_backtesting = SupportsBacktesting.BIASED

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._trading_mode = self._config["trading_mode"]
        self._stake_currency: str = self._config["stake_currency"]
        self._target_mode = "spot" if self._config["trading_mode"] == "futures" else "futures"
        self._selection_mode: str = self._pairlistconfig.get("selection_mode", "whitelist")
        self._info_key: str = self._pairlistconfig.get("info_key", "")
        self._info_compare_value: str = self._pairlistconfig.get("info_compare_value", "")
        self._refresh_period = self._pairlistconfig.get("refresh_period", 1800)
        self._pair_cache: FtTTLCache = FtTTLCache(maxsize=1, ttl=self._refresh_period)

    def short_desc(self) -> str:
        """
        Short whitelist method description - used for startup-messages
        """
        return (
            f"{self.name} - Returns {self._selection_mode} pairs by comparing "
            f"{self._info_key} matches {self._info_compare_value}."
        )

    @staticmethod
    def description() -> str:
        return "Filter pairs based upon any information in their market data."

    @staticmethod
    def available_parameters() -> dict[str, PairlistParameter]:
        return {
            "selection_mode": {
                "type": "option",
                "default": "all",
                "options": ["all", "whitelist", "blacklist"],
                "description": "Whether to include all pairs or whitelist or blacklist",
                "help": "Whether to include all pairs or whitelist or blacklist",
            },
            "info_key": {
                "type": "string",
                "default": "info.contractType",
                "description": "The key in the market data to compare against",
                "help": "The key in the market data to compare against",
            },
            "info_compare_value": {
                "type": "string",
                "default": "TRADIFI_PERPETUAL",
                "description": "The value to compare the key against",
                "help": "The value to compare the key against",
            },
            **IPairList.refresh_period_parameter(),
        }

    def filter_pairlist(self, pairlist: list[str], tickers: Tickers) -> list[str]:
        # if trading_mode not futures or all or values missing then return the pairlist
        if (self._trading_mode != "futures" or self._selection_mode == "all"
            or not self._info_key or not self._info_compare_value):
            return pairlist

        whitelist_or_blacklist = self._selection_mode == "whitelist"
        whitelist_pairlist: list[str] = []
        blacklist_pairlist: list[str] = []

        # loop through and add them to either list based on the market info check
        for pair in pairlist:
            market = self._exchange.markets[pair]
            if safe_value_nested(market, self._info_key, "") == self._info_compare_value:
                whitelist_pairlist.append(pair)
            else:
                blacklist_pairlist.append(pair)

        return whitelist_pairlist if whitelist_or_blacklist else blacklist_pairlist
