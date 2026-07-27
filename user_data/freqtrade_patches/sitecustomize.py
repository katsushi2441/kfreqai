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

# MEXC先物のバックテスト用: freqtradeはレバレッジ階層(notional/mmr)を要求するが、
# MEXCのtiersはnotional建てでなくvol建てのためNoneになり backtest が止まる。
# 実運用はレバ2倍・SL -6%で清算価格(約-50%)にはるか届かないので、tier精度は結果に
# 影響しない。全ペア共通の緩い1階層(mmr 1%・最大レバ100)を返して起動を通す。
try:
    from freqtrade.exchange.exchange import Exchange as _Exch

    def _flat_tiers(self):
        tm = str(getattr(self.trading_mode, "value", self.trading_mode) or "")
        if tm != "futures":
            return {}
        tier = [{"minNotional": 0, "maxNotional": None,
                 "maintenanceMarginRate": 0.01, "maxLeverage": 100, "maintAmt": 0.0}]
        out = {}
        for s, m in (self.markets or {}).items():
            if m.get("swap") or m.get("contract") or s.endswith(":USDT"):
                out[s] = tier
        return out
    _Exch.load_leverage_tiers = _flat_tiers
except Exception:
    pass
