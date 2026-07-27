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

# MEXC先物のバックテスト用: freqtradeはレバレッジ階層(notional/mmr建て)を要求するが、
# MEXCのtiersはvol建てでNoneになり backtest が止まる。実運用はレバ2倍・SL-6%で清算
# (約-50%)に届かないためtier精度は無関係。fill_leverage_tiersを差し替え、全先物ペアに
# 緩い1階層を直接セットして起動を通す(load_leverage_tiers差し替えでは呼ばれなかった)。
try:
    from freqtrade.exchange.exchange import Exchange as _Exch2

    def _fill_flat(self):
        tm = str(getattr(self.trading_mode, "value", self.trading_mode) or "")
        if tm != "futures":
            return
        tier = [{"minNotional": 0, "maxNotional": None,
                 "maintenanceMarginRate": 0.01, "maxLeverage": 100, "maintAmt": 0.0}]
        for s, m in (self.markets or {}).items():
            if m.get("swap") or m.get("contract") or s.endswith(":USDT"):
                self._leverage_tiers[s] = tier
    _Exch2.fill_leverage_tiers = _fill_flat
except Exception:
    pass
