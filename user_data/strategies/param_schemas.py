"""Dependency-free registry of user-tunable parameter schemas, keyed by strategy.

Kept separate from the strategy classes on purpose: the strategy imports talib
and freqtrade (only available inside the container), but the dashboard backend
(kurage-chat/chat_api.py, on the host) must read a strategy's schema WITHOUT
importing those. Both sides import this module; it pulls in nothing heavy.

A strategy sets `PARAM_SCHEMA = SCHEMAS["<ClassName>"]`. The dashboard renders one
generic form per strategy from its schema (see strategy_params.schema_for_ui).
Only numbers/enums are tunable; entry LOGIC changes still need code.
"""

SCHEMAS = {
    "KfreqaiParametricStrategy": [
        # -- entry --
        {"key": "ema_fast", "type": "int", "default": 12, "min": 2, "max": 100, "step": 1,
         "group": "entry", "label": {"ja": "短期EMA期間", "en": "Fast EMA period"}},
        {"key": "ema_slow", "type": "int", "default": 26, "min": 5, "max": 400, "step": 1,
         "group": "entry", "label": {"ja": "長期EMA期間", "en": "Slow EMA period"}},
        {"key": "rsi_period", "type": "int", "default": 14, "min": 2, "max": 50, "step": 1,
         "group": "entry", "label": {"ja": "RSI期間", "en": "RSI period"}},
        {"key": "rsi_entry_max", "type": "float", "default": 60.0, "min": 0.0, "max": 100.0, "step": 1.0,
         "group": "entry", "label": {"ja": "エントリー上限RSI(超で見送り)", "en": "Max RSI to enter"}},
        # -- exit --
        {"key": "rsi_exit_min", "type": "float", "default": 82.0, "min": 0.0, "max": 100.0, "step": 1.0,
         "group": "exit", "label": {"ja": "決済RSI下限", "en": "Min RSI to exit"}},
        {"key": "use_ema_cross_exit", "type": "bool", "default": False,
         "group": "exit", "label": {"ja": "EMAデッドクロスで決済", "en": "Exit on EMA cross-down"}},
        # -- risk --
        {"key": "stoploss_pct", "type": "float", "default": -6.0, "min": -50.0, "max": -0.5, "step": 0.5,
         "group": "risk", "label": {"ja": "ストップロス(%)", "en": "Stop-loss (%)"}},
        {"key": "peak_trail_trigger_pct", "type": "float", "default": 5.0, "min": 0.0, "max": 100.0, "step": 0.5,
         "group": "risk", "label": {"ja": "利確トレール発動益(%)", "en": "Peak-trail arm profit (%)"}},
        {"key": "peak_trail_giveback_pct", "type": "float", "default": 40.0, "min": 5.0, "max": 95.0, "step": 5.0,
         "group": "risk", "label": {"ja": "ピークからの押し戻し許容(%)", "en": "Give-back from peak (%)"}},
        # -- sizing: regime-adaptive position sizing (ATR% bands) --
        {"key": "regime_atr_narrow", "type": "float", "default": 1.0, "min": 0.1, "max": 10.0, "step": 0.1,
         "group": "sizing", "label": {"ja": "レジーム閾値: Narrow ATR%", "en": "Regime: Narrow ATR%"}},
        {"key": "regime_atr_standard", "type": "float", "default": 2.0, "min": 0.1, "max": 10.0, "step": 0.1,
         "group": "sizing", "label": {"ja": "レジーム閾値: Standard ATR%", "en": "Regime: Standard ATR%"}},
        {"key": "regime_atr_wide", "type": "float", "default": 3.0, "min": 0.1, "max": 15.0, "step": 0.1,
         "group": "sizing", "label": {"ja": "レジーム閾値: Wide ATR%", "en": "Regime: Wide ATR%"}},
        {"key": "pos_pct_narrow", "type": "float", "default": 40.0, "min": 5.0, "max": 100.0, "step": 5.0,
         "group": "sizing", "label": {"ja": "建玉%: Narrow", "en": "Position %: Narrow"}},
        {"key": "pos_pct_standard", "type": "float", "default": 70.0, "min": 5.0, "max": 100.0, "step": 5.0,
         "group": "sizing", "label": {"ja": "建玉%: Standard(基準)", "en": "Position %: Standard (ref)"}},
        {"key": "pos_pct_wide", "type": "float", "default": 60.0, "min": 5.0, "max": 100.0, "step": 5.0,
         "group": "sizing", "label": {"ja": "建玉%: Wide", "en": "Position %: Wide"}},
        {"key": "pos_pct_volatile", "type": "float", "default": 40.0, "min": 5.0, "max": 100.0, "step": 5.0,
         "group": "sizing", "label": {"ja": "建玉%: Volatile", "en": "Position %: Volatile"}},
        # (3) asset-tier size multipliers (spot analog of nofx per-class value caps)
        {"key": "pos_mult_majors", "type": "float", "default": 1.0, "min": 0.1, "max": 3.0, "step": 0.1,
         "group": "sizing", "label": {"ja": "サイズ乗数: BTC/ETH", "en": "Size mult: BTC/ETH"}},
        {"key": "pos_mult_alts", "type": "float", "default": 1.0, "min": 0.1, "max": 3.0, "step": 0.1,
         "group": "sizing", "label": {"ja": "サイズ乗数: アルト", "en": "Size mult: altcoins"}},
        # (4) anti-churn: cooldown + min-hold + noise band
        {"key": "reentry_cooldown_min", "type": "int", "default": 0, "min": 0, "max": 1440, "step": 5,
         "group": "churn", "label": {"ja": "再エントリー・クールダウン(分)", "en": "Re-entry cooldown (min)"}},
        {"key": "min_hold_min", "type": "int", "default": 0, "min": 0, "max": 1440, "step": 5,
         "group": "churn", "label": {"ja": "最小保有時間(分)", "en": "Minimum hold (min)"}},
        {"key": "profit_exit_pct", "type": "float", "default": 3.0, "min": 0.1, "max": 100.0, "step": 0.5,
         "group": "churn", "label": {"ja": "最小保有を無視する利益(%)", "en": "Profit that overrides min-hold (%)"}},
        {"key": "loss_exit_pct", "type": "float", "default": -3.0, "min": -50.0, "max": -0.1, "step": 0.5,
         "group": "churn", "label": {"ja": "最小保有を無視する損失(%)", "en": "Loss that overrides min-hold (%)"}},
        {"key": "noise_band_pct", "type": "float", "default": 0.0, "min": 0.0, "max": 10.0, "step": 0.1,
         "group": "churn", "label": {"ja": "ノイズ帯(±%内は決済しない)", "en": "Noise band (no exit within ±%)"}},
        # (5) DCA / position adjustment (off by default)
        {"key": "enable_dca", "type": "bool", "default": False,
         "group": "dca", "label": {"ja": "DCA(押し目買い増し)を有効化", "en": "Enable DCA (add on dips)"}},
        {"key": "dca_trigger_pct", "type": "float", "default": -3.0, "min": -50.0, "max": -0.5, "step": 0.5,
         "group": "dca", "label": {"ja": "DCA発動の含み損(%)", "en": "DCA trigger drawdown (%)"}},
        {"key": "dca_max_orders", "type": "int", "default": 0, "min": 0, "max": 5, "step": 1,
         "group": "dca", "label": {"ja": "DCA最大回数", "en": "Max DCA adds"}},
        {"key": "dca_stake_mult", "type": "float", "default": 0.5, "min": 0.1, "max": 2.0, "step": 0.1,
         "group": "dca", "label": {"ja": "DCA1回の初回比", "en": "DCA add size vs first entry"}},
        # (6) box breakout confirmation gate (ON by default: tuned defaults use it)
        {"key": "enable_breakout_gate", "type": "bool", "default": True,
         "group": "breakout", "label": {"ja": "ブレイク確認ゲートを有効化", "en": "Enable breakout-confirm gate"}},
        {"key": "box_lookback", "type": "int", "default": 24, "min": 5, "max": 500, "step": 1,
         "group": "breakout", "label": {"ja": "ボックス参照本数", "en": "Box lookback (candles)"}},
        {"key": "breakout_confirm_candles", "type": "int", "default": 1, "min": 1, "max": 20, "step": 1,
         "group": "breakout", "label": {"ja": "ブレイク確認本数", "en": "Breakout confirm candles"}},
    ],
}
