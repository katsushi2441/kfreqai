"""TradFi pair list filter"""

import logging

from freqtrade.exchange.exchange_types import Tickers
from freqtrade.misc import safe_value_nested
from freqtrade.plugins.pairlist.IPairList import IPairList, PairlistParameter, SupportsBacktesting
from freqtrade.util import FtTTLCache


logger = logging.getLogger(__name__)


class TradFiPairList(IPairList):
    is_pairlist_generator = True
    supports_backtesting = SupportsBacktesting.BIASED

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._trading_mode = self._config["trading_mode"]
        self._stake_currency: str = self._config["stake_currency"]
        self._target_mode = "spot" if self._config["trading_mode"] == "futures" else "futures"
        self._tradfi_mode: str = self._pairlistconfig.get("tradfi_mode", "all")
        self._tradfi_key: str = self._pairlistconfig.get("tradfi_key", "info.contractType")
        self._tradfi_compare_value: str = self._pairlistconfig.get(
            "tradfi_compare_value", "TRADIFI_PERPETUAL"
        )
        self._refresh_period = self._pairlistconfig.get("refresh_period", 1800)
        self._pair_cache: FtTTLCache = FtTTLCache(maxsize=1, ttl=self._refresh_period)

    def short_desc(self) -> str:
        """
        Short whitelist method description - used for startup-messages
        """
        tradfi_mode = self._tradfi_mode
        msg = f"{self.name} - Pairs that exists on {tradfi_mode.capitalize()}."
        return msg

    @staticmethod
    def description() -> str:
        return "Filter pairs if they are TradFi pairs or not."

    @staticmethod
    def available_parameters() -> dict[str, PairlistParameter]:
        return {
            "tradfi_mode": {
                "type": "option",
                "default": "all",
                "options": ["all", "tradfi_only", "crypto_only"],
                "description": "Select pairs based on whether they are TradFi pairs or not",
                "help": "Select pairs based on whether they are TradFi pairs or not",
            },
            "tradfi_key": {
                "type": "string",
                "default": "info.contractType",
                "description": "The key to in the market data that indicates if it's a TradFi pair",
                "help": "The key to in the market data that indicates if it's a TradFi pair or not",
            },
            "tradfi_compare_value": {
                "type": "string",
                "default": "TRADIFI_PERPETUAL",
                "description": "The value to compare the key against",
                "help": "The value to compare the key against",
            },
            **IPairList.refresh_period_parameter(),
        }

    def filter_pairlist(self, pairlist: list[str], tickers: Tickers) -> list[str]:
        # if trading_mode not futures or mode is all then just return the pairlist as is
        if self._trading_mode != "futures" or self._tradfi_mode == "all":
            return pairlist

        tradfi_only = self._tradfi_mode == "tradfi_only"
        tradfi_pairlist: list[str] = []
        crypto_pairlist: list[str] = []

        # loop through and add them to either list based on the contract type
        for pair in pairlist:
            market = self._exchange.markets[pair]
            if safe_value_nested(market, self._tradfi_key, "") == self._tradfi_compare_value:
                tradfi_pairlist.append(pair)
            else:
                crypto_pairlist.append(pair)

        return tradfi_pairlist if tradfi_only else crypto_pairlist
