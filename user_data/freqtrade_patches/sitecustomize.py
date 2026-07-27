"""起動時フック: freqtradeにMEXC先物(futures/isolated)を許可する。

PYTHONPATHにこのディレクトリを入れると、Python起動時に自動importされる。
戦略ファイル内でのパッチ適用では「設定検証 → 戦略読込」の順のため間に合わないので、
プロセス起動時に当てる必要がある。詳細な経緯は mexc_futures.py を参照。
"""
try:
    from freqtrade.enums import MarginMode, TradingMode
    from freqtrade.exchange.exchange import Exchange

    _pair = (TradingMode.FUTURES, MarginMode.ISOLATED)
    _pairs = list(getattr(Exchange, "_supported_trading_mode_margin_pairs", []))
    if _pair not in _pairs:
        _pairs.append(_pair)
        Exchange._supported_trading_mode_margin_pairs = _pairs
except Exception:  # freqtrade以外のプロセスでは何もしない
    pass
