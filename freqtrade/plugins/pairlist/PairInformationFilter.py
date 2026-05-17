"""Pair Information filter"""

import logging

from freqtrade.exceptions import OperationalException
from freqtrade.exchange.exchange_types import Tickers
from freqtrade.misc import safe_value_nested
from freqtrade.plugins.pairlist.IPairList import IPairList, PairlistParameter, SupportsBacktesting
from freqtrade.util import FtTTLCache


logger = logging.getLogger(__name__)


class PairInformationFilter(IPairList):
    supports_backtesting = SupportsBacktesting.BIASED

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        if "info_key" not in self._pairlistconfig:
            raise OperationalException(
                "`info_key` not specified. Please check your configuration "
                'for "pairlist.config.info_key"'
            )
        if "info_compare_value" not in self._pairlistconfig:
            raise OperationalException(
                "`info_compare_value` not specified. Please check your configuration "
                'for "pairlist.config.info_compare_value"'
            )

        self._trading_mode = self._config["trading_mode"]
        self._stake_currency: str = self._config["stake_currency"]
        self._target_mode = "spot" if self._config["trading_mode"] == "futures" else "futures"
        self._selection_mode: str = self._pairlistconfig.get("selection_mode", "whitelist")
        self._info_key: str = self._pairlistconfig.get("info_key", "")
        self._info_compare_value: str = self._pairlistconfig.get("info_compare_value", "")
        self._refresh_period = self._pairlistconfig.get("refresh_period", 1800)
        self._pair_cache: FtTTLCache = FtTTLCache(maxsize=1, ttl=self._refresh_period)

        if self._selection_mode not in ["whitelist", "blacklist"]:
            raise OperationalException(
                '`selection_mode` not configured correctly.'
                'Supported Modes are "whitelist" and "blacklist"'
            )

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
                "default": "whitelist",
                "options": ["whitelist", "blacklist"],
                "description": "Whether to use filter as whitelist or blacklist",
                "help": "Whether to use filter as whitelist or blacklist",
            },
            "info_key": {
                "type": "string",
                "default": "",
                "description": "The key in the market data to compare against",
                "help": "The key in the market data to compare against",
            },
            "info_compare_value": {
                "type": "string",
                "default": "",
                "description": "The value to compare the key against",
                "help": "The value to compare the key against",
            },
            **IPairList.refresh_period_parameter(),
        }

    def filter_pairlist(self, pairlist: list[str], tickers: Tickers) -> list[str]:
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
