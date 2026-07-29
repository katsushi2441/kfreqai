"""kfreqaihl(Hyperliquid版)のテナント別・調整可能パラメータのスキーマ。

user_data/strategies/param_schemas.py と同じ思想: 数値/ON-OFFの範囲を
あらかじめ決めておき、チャット(Kurageさん)経由でもこの範囲内でしか
変更できないようにする。任意コード実行はさせない(バイブコーディングでは
なく、会話→パラメータのバイブトレーディング)。

Hyperliquidはperp(無期限先物)なので、現物版のスキーマと違い
・ロング/ショート両方向を扱える(is_long_enabled / is_short_enabled)
・レバレッジがある(leverage)
という項目を追加している。
"""

SCHEMAS = {
    "HLTrendPerpStrategy": [
        # -- entry --
        {"key": "ema_fast", "type": "int", "default": 12, "min": 2, "max": 100, "step": 1,
         "group": "entry", "label": {"ja": "短期EMA期間", "en": "Fast EMA period"}},
        {"key": "ema_slow", "type": "int", "default": 26, "min": 5, "max": 400, "step": 1,
         "group": "entry", "label": {"ja": "長期EMA期間", "en": "Slow EMA period"}},
        {"key": "box_lookback", "type": "int", "default": 24, "min": 5, "max": 500, "step": 1,
         "group": "entry", "label": {"ja": "ボックス参照本数", "en": "Box lookback (candles)"}},
        {"key": "is_long_enabled", "type": "bool", "default": True,
         "group": "entry", "label": {"ja": "ロングを有効化", "en": "Enable long entries"}},
        {"key": "is_short_enabled", "type": "bool", "default": True,
         "group": "entry", "label": {"ja": "ショートを有効化", "en": "Enable short entries"}},
        # ブレイク確認ゲート。既定OFF: 頻度の逆算(実測)でON時は1銘柄62日に1回まで
        # 減り検証不能なため。ONにすると質は上がるが回数は激減(約30分の1)。
        {"key": "enable_breakout_gate", "type": "bool", "default": False,
         "group": "entry", "label": {"ja": "ブレイク確認ゲート(ONで回数激減)", "en": "Breakout-confirm gate (ON = far fewer trades)"}},
        # -- risk / leverage / slots --
        # max_open_trades = 同時保有の「枠」。kfreqai本番と同じ概念(本番は10枠)。
        # 各枠の証拠金 = 口座残高 / 枠数、名目 = それ×leverage。
        {"key": "max_open_trades", "type": "int", "default": 10, "min": 1, "max": 20, "step": 1,
         "group": "risk", "label": {"ja": "同時保有の枠数", "en": "Max open positions (slots)"}},
        {"key": "leverage", "type": "int", "default": 2, "min": 1, "max": 5, "step": 1,
         "group": "risk", "label": {"ja": "レバレッジ倍率(1〜5倍。清算距離に直結)", "en": "Leverage (1-5x)"}},
        {"key": "stoploss_pct", "type": "float", "default": -6.0, "min": -30.0, "max": -1.0, "step": 0.5,
         "group": "risk", "label": {"ja": "ストップロス(%、証拠金に対して)", "en": "Stop-loss (% of margin)"}},
        {"key": "peak_trail_trigger_pct", "type": "float", "default": 4.0, "min": 0.0, "max": 100.0, "step": 0.5,
         "group": "risk", "label": {"ja": "利確トレール発動益(%)", "en": "Peak-trail arm profit (%)"}},
        {"key": "peak_trail_giveback_pct", "type": "float", "default": 25.0, "min": 5.0, "max": 95.0, "step": 5.0,
         "group": "risk", "label": {"ja": "ピークからの押し戻し許容(%)", "en": "Give-back from peak (%)"}},
        # 各枠のサイズ調整(任意): 枠あたり証拠金(=残高/枠数)にこの倍率をかける。
        # 100%=フル。kfreqaiのcustom_stake_amountのレジーム縮小に相当する簡易版。
        {"key": "slot_size_pct", "type": "float", "default": 30.0, "min": 10.0, "max": 100.0, "step": 5.0,
         "group": "risk", "label": {"ja": "1枠あたりサイズ%(枠証拠金に対して)", "en": "Per-slot size (% of slot margin)"}},
        # -- churn --
        {"key": "reentry_cooldown_min", "type": "int", "default": 60, "min": 0, "max": 1440, "step": 5,
         "group": "churn", "label": {"ja": "再エントリー・クールダウン(分)", "en": "Re-entry cooldown (min)"}},
    ],
}

DEFAULT_STRATEGY = "HLTrendPerpStrategy"
DEFAULT_MAX_OPEN_TRADES = 10  # kfreqai本番と同じ既定の枠数
