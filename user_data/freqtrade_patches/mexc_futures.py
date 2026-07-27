"""MEXC先物をfreqtradeで使えるようにする最小パッチ(2026-07-27)。

freqtradeはMEXCを「spotのみ対応」として内部表に持っており、
config で trading_mode=futures にすると
  ConfigurationError: Freqtrade does not support 'isolated' 'futures' on MEXC Global.
で起動を拒否する。ただしこれは freqtrade 側の対応表の話で、実体(ccxt)は
MEXC の swap 市場1026件・先物残高取得・ティッカー取得すべて成功することを実測済み
(2026-07-27)。またAPIキーの先物発注権限も管理画面で有効。

そこで freqtrade 本体には手を入れず、Exchange クラスの対応表に
(FUTURES, ISOLATED) を足すだけのモンキーパッチを、strategy 読込時に適用する。
※これは freqtrade が公式検証していない構成なので、**dry-run 前提**で使うこと。
  実弾に進む場合は、約定・手数料・清算価格の挙動を実測で確かめてから。
"""
from freqtrade.enums import MarginMode, TradingMode
from freqtrade.exchange.exchange import Exchange

_PAIR = (TradingMode.FUTURES, MarginMode.ISOLATED)


def apply() -> bool:
    """MEXCのExchangeサブクラスに (futures, isolated) を許可する。適用したらTrue。"""
    applied = False
    for cls in Exchange.__subclasses__():
        if cls.__name__.lower().startswith("mexc"):
            pairs = list(getattr(cls, "_supported_trading_mode_margin_pairs", []))
            if _PAIR not in pairs:
                pairs.append(_PAIR)
                cls._supported_trading_mode_margin_pairs = pairs
                applied = True
    if not applied:
        # MEXC専用クラスが無い(=汎用Exchangeで動く)場合は基底クラス側に追加する
        pairs = list(getattr(Exchange, "_supported_trading_mode_margin_pairs", []))
        if _PAIR not in pairs:
            pairs.append(_PAIR)
            Exchange._supported_trading_mode_margin_pairs = pairs
            applied = True
    return applied
