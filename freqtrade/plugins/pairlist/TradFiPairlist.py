"""Cross Market pair list filter"""

from importlib import abc
import logging

from freqtrade.constants import PairPrefixes
from freqtrade.exchange.exchange_types import Tickers
from freqtrade.plugins.pairlist.IPairList import IPairList, PairlistParameter, SupportsBacktesting
from freqtrade.util import FtTTLCache


logger = logging.getLogger(__name__)


class TradFiPairList(IPairList):
    is_pairlist_generator = True
    supports_backtesting = SupportsBacktesting.BIASED

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
    
        self._trading_mode = self._config["trading_mode"]
        self._tradfi_mode: str = self._pairlistconfig.get("tradfi_mode", "all")
        self._stake_currency: str = self._config["stake_currency"]
        self._target_mode = "spot" if self._config["trading_mode"] == "futures" else "futures"
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
            **IPairList.refresh_period_parameter(),
        }

    def gen_pairlist(self, tickers: Tickers) -> list[str]:
        """
        Generate the pairlist
        :param tickers: Tickers (from exchange.get_tickers). May be cached.
        :return: List of pairs
        """
        # Generate dynamic whitelist
        # Must always run if this pairlist is the first in the list.
        pairlist = self._pair_cache.get("pairlist")
        if pairlist:
            # Item found - no refresh necessary
            return pairlist.copy()
        else:
            # Use fresh pairlist
            # Check if pair quote currency equals to the stake currency.
            _pairlist = [
                k
                for k in self._exchange.get_markets(
                    quote_currencies=[self._stake_currency], tradable_only=True, active_only=True
                ).keys()
            ]

            _pairlist = self.verify_blacklist(_pairlist, logger.info)

            pairlist = self.filter_pairlist(_pairlist, tickers)
            self._pair_cache["pairlist"] = pairlist.copy()

        return pairlist

    def filter_pairlist(self, pairlist: list[str], tickers: Tickers) -> list[str]:
        
        # if trading_mode futures or all then just return the pairlist as is, no need to check for tradfi pairs
        if (self._trading_mode != "futures" or self._tradfi_mode == "all"):
            return pairlist
        
        # get the market data from the exchange and check if the pair is a tradfi pair or not based on the contract type        
        tradfi_only = self._tradfi_mode == "tradfi_only"
        tradfi_pairlist: list[str] = []
        crypto_pairlist: list[str] = []

        # loop through and add them to either list based on the contract type
        for pair in pairlist:
            
            if self._exchange.is_tradfi_pair(pair):
                tradfi_pairlist.append(pair)
            else:
                crypto_pairlist.append(pair)
        
        return tradfi_pairlist if tradfi_only else crypto_pairlist
