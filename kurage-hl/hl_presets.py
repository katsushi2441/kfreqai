"""kfreqaihl の「戦略プリセット」と、動いている戦略の人間向け説明。

案A(ユーザー合意): 戦略の頭脳は1つ(HLTrendPerpStrategy / strategy_core)のまま、
パラメータの束に「堅実型/標準型/積極型/スキャル型」という名前と説明を付けて
選べるようにする。中身は同じ共通コアなので、本番ループ・バックテストの分岐は不要
(プリセット=hl_schemasの範囲内のパラメータ上書き集合)。

ダッシュボードに「今どの戦略・どのプリセットで動いていて、どう調整できるか」を
出すための説明文もここに集約する(PHPはこれを表示するだけ)。
"""
import hl_schemas

# 動いている戦略そのものの説明(kfreqai本番と共通の頭脳)。人間が読んで分かる日本語。
STRATEGY_INFO = {
    "key": "HLTrendPerpStrategy",
    "name": "トレンド追随（EMAクロス）戦略",
    "tagline": "移動平均のクロスでトレンドに乗り、行き過ぎ(RSI)とピーク押し戻しで手仕舞い。",
    "market": "Hyperliquidの主要50銘柄(無期限先物)を巡回。ロング/ショート両建て可。",
    "how": [
        "エントリー：短期EMAが長期EMAを上抜けたらロング（下抜けでショート）。RSIが過熱していないことを確認する。",
        "ブレイク確認ゲート：ONにすると直近レンジのブレイクも条件に加わり、回数は激減するが質は上がる（既定OFF＝回数優先）。",
        "決済：①ストップロス（証拠金比%） ②ピーク利益から一定%押し戻したらトレール利確 ③RSIが逆側で過熱したら決済。",
        "枠（同時保有数）：最大N銘柄まで同時に持つ。各枠の証拠金＝残高÷枠数、名目＝それ×レバレッジ。",
    ],
    "adjust": "「戦略設定」タブでプリセットを選ぶか、数値を直接調整できます。「戦略会議」でKurageさんに"
              "『レバを上げて』『積極型にして』のように話しかけても変えられます（コード変更なし＝バイブトレーディング）。",
}

# プリセット。paramsは hl_schemas の範囲内のキーだけを上書きする(未指定は既定値のまま)。
# 標準型 = 既定値そのもの(頻度逆算済のゲートOFF+両建て+50銘柄)。
PRESETS = [
    {
        "id": "conservative",
        "name": "堅実型",
        "emoji": "🛡️",
        "desc": "少数精鋭・低レバ。ブレイク確認ゲートONで回数を絞り、質の高い場面だけ狙う。値動きに強いが取引は少なめ。",
        "params": {
            "leverage": 1, "max_open_trades": 5, "slot_size_pct": 60.0,
            "stoploss_pct": -4.0, "enable_breakout_gate": True,
            "is_long_enabled": True, "is_short_enabled": True,
            "ema_fast": 12, "ema_slow": 26,
            "peak_trail_trigger_pct": 3.0, "peak_trail_giveback_pct": 20.0,
        },
    },
    {
        "id": "standard",
        "name": "標準型",
        "emoji": "⚖️",
        "desc": "バランス型（既定）。ゲートOFFで回数を確保しつつ、レバ2倍・10枠・両建てで検証しやすい設定。",
        "params": {
            "leverage": 2, "max_open_trades": 10, "slot_size_pct": 100.0,
            "stoploss_pct": -6.0, "enable_breakout_gate": False,
            "is_long_enabled": True, "is_short_enabled": True,
            "ema_fast": 12, "ema_slow": 26,
            "peak_trail_trigger_pct": 4.0, "peak_trail_giveback_pct": 25.0,
        },
    },
    {
        "id": "aggressive",
        "name": "積極型",
        "emoji": "🔥",
        "desc": "高レバ・多枠・回数重視。短期EMAを速くして早く乗る。リターンもリスクも大きい上級者向け。",
        "params": {
            "leverage": 4, "max_open_trades": 15, "slot_size_pct": 100.0,
            "stoploss_pct": -8.0, "enable_breakout_gate": False,
            "is_long_enabled": True, "is_short_enabled": True,
            "ema_fast": 8, "ema_slow": 21,
            "peak_trail_trigger_pct": 5.0, "peak_trail_giveback_pct": 30.0,
        },
    },
    {
        "id": "scalp",
        "name": "スキャル型",
        "emoji": "⚡",
        "desc": "短期・小さく多く。EMAを速くしトレールを浅くして、細かく利確しながら回転数を上げる。",
        "params": {
            "leverage": 3, "max_open_trades": 12, "slot_size_pct": 80.0,
            "stoploss_pct": -5.0, "enable_breakout_gate": False,
            "is_long_enabled": True, "is_short_enabled": True,
            "ema_fast": 5, "ema_slow": 13,
            "peak_trail_trigger_pct": 2.0, "peak_trail_giveback_pct": 40.0,
            "reentry_cooldown_min": 30,
        },
    },
]

_PRESET_BY_ID = {p["id"]: p for p in PRESETS}
DEFAULT_PRESET = "standard"
PRESET_MARKER = "__preset"  # params_jsonにプリセット名を残す予約キー(戦略パラメータではない)

# FX/商品/指数(builder-dex "xyz")向けの既定パラメータ。クリプトとは別プロファイル。
# FXは低ボラ(EUR/USD≈0.29%/日)なのでクリプト用の-6%ストップは永遠に効かない。
# 2026-07-26のmainnet実データ・スイープで選定: ストップ-2.5%/トレール発動1.5%が最良
# (60日・12銘柄で+11.0%・2.2回/日・勝率70.9%・DD8.3%)。
# ※単一期間のバックテストであり将来を保証しない。実運用前に複数期間で再検証すること。
FX_PRESET_PARAMS = {
    "ema_fast": 12, "ema_slow": 26, "box_lookback": 24,
    "is_long_enabled": True, "is_short_enabled": True, "enable_breakout_gate": False,
    "max_open_trades": 8, "leverage": 3, "slot_size_pct": 100.0,
    "stoploss_pct": -2.5, "peak_trail_trigger_pct": 1.5, "peak_trail_giveback_pct": 30.0,
    "reentry_cooldown_min": 60,
}


def get_preset(preset_id):
    return _PRESET_BY_ID.get(preset_id)


def preset_params(preset_id):
    p = _PRESET_BY_ID.get(preset_id)
    return dict(p["params"]) if p else {}


def _schema_defaults():
    schema = hl_schemas.SCHEMAS[hl_schemas.DEFAULT_STRATEGY]
    return {s["key"]: s["default"] for s in schema}


def effective_params(stored):
    """保存値(stored, PRESET_MARKER含む)にスキーマ既定を敷いた実効パラメータ。"""
    p = _schema_defaults()
    for k, v in (stored or {}).items():
        if k == PRESET_MARKER:
            continue
        if k in p:
            p[k] = v
    return p


def infer_preset(stored):
    """保存値から現在のプリセットを判定。明示マーカーがあればそれ、無ければ
    実効パラメータが一致するプリセットを探し、無ければ 'custom'。"""
    if stored and stored.get(PRESET_MARKER):
        pid = stored[PRESET_MARKER]
        return pid if (pid in _PRESET_BY_ID or pid == "custom") else "custom"
    eff = effective_params(stored)
    for preset in PRESETS:
        if all(eff.get(k) == v for k, v in preset["params"].items()):
            return preset["id"]
    return "custom"


def presets_public():
    """PHPに渡す軽量なプリセット一覧(paramsも含める。設定画面のプレビュー用)。"""
    return [{"id": p["id"], "name": p["name"], "emoji": p["emoji"],
             "desc": p["desc"], "params": p["params"]} for p in PRESETS]
