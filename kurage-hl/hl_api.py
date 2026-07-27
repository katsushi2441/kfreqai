"""kfreqaihl — Hyperliquidマルチテナント版のバックエンド(方式B: 自前軽量サービス)。

kfreqai.php/chat_api.pyと違い、1プロセスで**複数ユーザー**を扱う。各ユーザーは
・自分のメインHyperliquidアカウント(資金はここに残る。カストディしない)
・このサーバーが生成したAgent Wallet(取引専用・出金不可の委任鍵)
を持ち、tenant_store.pyのsqliteに1行ずつ台帳を持つ。

チャットのLLMは合意した設計どおり:
・xb_bittensor(管理者)は無料のgemma4(ローカルOllama)
・それ以外の一般ユーザーはDeepSeek、x402の支払い証明ヘッダーを要求
  (今回のPhase1では支払い"検証"はスタブ。実際のオンチェーン検証は
   url2ai/apps/llm-gateway/server-jpyc-url2brain.js と同じ仕組みを
   前段のゲートウェイとして立てるのが次フェーズ)

kfreqai.php(kurage_web)はheteml(別ホスト)で動いており、127.0.0.1縛りでは
そこから到達できない(advisory_api.pyの127.0.0.1限定はこのホスト上のnginx
経由の別パターンで、ここには使えない)。よってchat_api.py(:18322)と同じく
0.0.0.0で公開し、PHP<->python間は共有トークン(X-Hl-Token / HL_INTERNAL_TOKEN)
で認証する。テナントの秘密鍵(agent_private_key)はどのレスポンスにも
絶対に含めない(agent_addressだけ返す)ことで、公開ポートでも鍵は漏れない。

発注(実弾)はまだ実装しない。hl_connector.place_order()はDRY-RUNガード
がかかっており、HL_LIVE_TRADING=1を明示的に立てない限り例外を投げる。

起動: uvicorn hl_api:app --host 0.0.0.0 --port 18339
"""
import os
import sys

from fastapi import Body, FastAPI, Header, HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "kurage-advisory"))
import llm_client  # noqa: E402  (claude/codex/gemma4 fallback; here only call_gemma is used)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "user_data", "strategies"))
import strategy_params as _sp  # noqa: E402  (reuse _coerce/validate_schema only; no shared file)

import strategy_core  # noqa: E402  (指標: brain判断のevidence用)

import autoreload  # noqa: E402
import deepseek_client  # noqa: E402
import hl_brain_client as brain  # noqa: E402  (kcbrain/kfxbrain 判断)
import hl_connector  # noqa: E402
import hl_loop  # noqa: E402
import hl_presets  # noqa: E402
import hl_schemas  # noqa: E402
import tenant_store  # noqa: E402

autoreload.start()  # ソース変更で自動再起動(手動restart不要)

ADMIN_USERNAME = "xb_bittensor"
INTERNAL_TOKEN = os.environ.get("HL_INTERNAL_TOKEN", "")
MAX_MESSAGE_CHARS = 1000

app = FastAPI(title="kfreqaihl", version="0.1")


def _check_internal_token(token: str):
    if not INTERNAL_TOKEN:
        raise HTTPException(503, "HL_INTERNAL_TOKEN not configured on the server")
    if token != INTERNAL_TOKEN:
        raise HTTPException(403, "forbidden")


def _clean_username(username):
    username = (username or "").strip().lstrip("@")
    if not username or len(username) > 64:
        raise HTTPException(422, "invalid username")
    return username


@app.post("/api/tenant/register")
def tenant_register(payload: dict = Body(...), x_hl_token: str = Header(default="")):
    _check_internal_token(x_hl_token)
    username = _clean_username(payload.get("username"))
    tenant = tenant_store.get_or_create(username, hl_connector.generate_agent_wallet)
    return {
        "username": tenant["username"],
        "agent_address": tenant["agent_address"],
        "main_wallet_address": tenant["main_wallet_address"],
        "agent_approved": bool(tenant["agent_approved"]),
        "is_testnet": hl_connector.USE_TESTNET,
    }


@app.post("/api/tenant/main-wallet")
def tenant_set_main_wallet(payload: dict = Body(...), x_hl_token: str = Header(default="")):
    _check_internal_token(x_hl_token)
    username = _clean_username(payload.get("username"))
    address = (payload.get("address") or "").strip()
    if not address.startswith("0x") or len(address) != 42:
        raise HTTPException(422, "address must be a 0x... EVM address")
    tenant_store.get_or_create(username, hl_connector.generate_agent_wallet)
    tenant_store.set_main_wallet(username, address, approved=False)
    return {"username": username, "main_wallet_address": address, "agent_approved": False}


@app.post("/api/tenant/confirm-approval")
def tenant_confirm_approval(payload: dict = Body(...), x_hl_token: str = Header(default="")):
    """委任完了の確定。自己申告ではなく、extraAgentsでオンチェーンに実際に
    Agentが登録されているかを検証してから承認印を立てる(モック時は検証スキップ)。"""
    _check_internal_token(x_hl_token)
    username = _clean_username(payload.get("username"))
    tenant = tenant_store.get_or_create(username, hl_connector.generate_agent_wallet)
    if not tenant.get("main_wallet_address"):
        raise HTTPException(422, "main wallet not registered yet")
    try:
        ok = hl_connector.agent_is_approved_onchain(
            tenant["main_wallet_address"], tenant["agent_address"])
    except Exception as exc:
        raise HTTPException(502, "on-chain verify failed: %s" % str(exc)[:150])
    if not ok:
        # まだオンチェーンに委任が見つからない(署名/送信が未完了)。承認印は立てない。
        return {"username": username, "agent_approved": False,
                "verified": False,
                "message": "オンチェーンにAgent委任が見つかりません。委任署名を完了してください。"}
    tenant_store.mark_approved(username)
    return {"username": username, "agent_approved": True, "verified": True}


@app.get("/api/dashboard")
def dashboard(username: str, x_hl_token: str = Header(default="")):
    """kfreqai本番サマリ相当(残高/保有ポジション/約定履歴/日次損益)を1回で返す。
    加えてウォレット委任の進捗(agent_approved等)も同梱し、PHPが1コールで描ける形に。"""
    _check_internal_token(x_hl_token)
    username = _clean_username(username)
    tenant = tenant_store.get_or_create(username, hl_connector.generate_agent_wallet)
    out = {
        "username": tenant["username"],
        "agent_address": tenant["agent_address"],
        "main_wallet_address": tenant["main_wallet_address"],
        "agent_approved": bool(tenant["agent_approved"]),
        "is_testnet": hl_connector.USE_TESTNET,
        "live_trading_enabled": hl_connector.LIVE_TRADING,
        "mock": hl_connector.MOCK,
        "max_open_trades": int(hl_presets.effective_params(
            tenant_store.get_params(username)).get("max_open_trades",
            hl_schemas.DEFAULT_MAX_OPEN_TRADES)),
        "strategy_name": hl_presets.STRATEGY_INFO["name"],
        "current_preset": _current_preset(username),
        "unified_enabled": False,
        "dashboard": None,
    }
    if tenant["main_wallet_address"]:
        try:
            out["dashboard"] = hl_connector.get_dashboard(tenant["main_wallet_address"])
        except Exception as exc:
            out["dashboard_error"] = str(exc)[:200]
        # Unified Accountが既に有効なら、有効化ボタンは隠す(一度きりでよい)
        try:
            out["unified_enabled"] = hl_connector.is_unified_account(tenant["main_wallet_address"])
        except Exception:
            pass
    return out


@app.get("/api/tenant/status")
def tenant_status(username: str, x_hl_token: str = Header(default="")):
    _check_internal_token(x_hl_token)
    username = _clean_username(username)
    tenant = tenant_store.get_or_create(username, hl_connector.generate_agent_wallet)
    out = {
        "username": tenant["username"],
        "agent_address": tenant["agent_address"],
        "main_wallet_address": tenant["main_wallet_address"],
        "agent_approved": bool(tenant["agent_approved"]),
        "is_testnet": hl_connector.USE_TESTNET,
        "live_trading_enabled": hl_connector.LIVE_TRADING,
        "mock": hl_connector.MOCK,
        "snapshot": None,
    }
    if tenant["main_wallet_address"]:
        try:
            out["snapshot"] = hl_connector.get_account_snapshot(tenant["main_wallet_address"])
        except Exception as exc:
            out["snapshot_error"] = str(exc)[:200]
    return out


# ---------------------------------------------------------------------------
# 戦略パラメータ(テナントごと)。管理画面のスキーマ駆動パターンをそのまま流用。
# ---------------------------------------------------------------------------

@app.get("/api/strategy-schema")
def strategy_schema(strategy: str = hl_schemas.DEFAULT_STRATEGY):
    schema = hl_schemas.SCHEMAS.get(strategy)
    if not schema:
        raise HTTPException(404, "unknown strategy")
    _sp.validate_schema(schema)
    return {"strategy": strategy, "schema": schema}


@app.get("/api/strategy-params")
def strategy_params_get(username: str, x_hl_token: str = Header(default="")):
    _check_internal_token(x_hl_token)
    username = _clean_username(username)
    schema = hl_schemas.SCHEMAS[hl_schemas.DEFAULT_STRATEGY]
    stored = tenant_store.get_params(username)
    effective = {}
    for spec in schema:
        key = spec["key"]
        ok, val = _sp._coerce(spec, stored[key]) if key in stored else (False, None)
        effective[key] = val if ok else spec["default"]
    return {"strategy": hl_schemas.DEFAULT_STRATEGY,
            "params": [dict(spec, value=effective[spec["key"]]) for spec in schema]}


def _write_tenant_params(username, updates):
    # get_or_create必須: 行が無い状態でset_params()のUPDATEを呼ぶと0件更新で
    # 静かに失敗し、chatが「反映した」と嘘の返事をすることになる(実際に踏んだバグ)。
    tenant_store.get_or_create(username, hl_connector.generate_agent_wallet)
    schema = hl_schemas.SCHEMAS[hl_schemas.DEFAULT_STRATEGY]
    by_key = {s["key"]: s for s in schema}
    stored = tenant_store.get_params(username)
    rejected = {}
    for key, value in (updates or {}).items():
        spec = by_key.get(key)
        if spec is None:
            rejected[key] = "unknown key"
            continue
        ok, val = _sp._coerce(spec, value)
        if ok:
            stored[key] = val
        else:
            rejected[key] = "invalid value"
    # 手動でパラメータを触ったら固定プリセット名の印は外す。以後は値一致で
    # プリセットを推定する(たまたまプリセットと一致すればそのプリセット表示、
    # そうでなければ「カスタム」)。プリセット適用はapply_presetが印を付け直す。
    stored.pop(hl_presets.PRESET_MARKER, None)
    tenant_store.set_params(username, stored)
    return rejected


def _current_preset(username):
    return hl_presets.infer_preset(tenant_store.get_params(username))


def _apply_preset(username, preset_id):
    """プリセットを適用: そのプリセットのパラメータ束で保存値を置き換え、印を付ける。
    未指定キーはスキーマ既定に戻る(effective_paramsが敷く)。"""
    preset = hl_presets.get_preset(preset_id)
    if not preset:
        return False
    tenant_store.get_or_create(username, hl_connector.generate_agent_wallet)
    stored = dict(preset["params"])
    stored[hl_presets.PRESET_MARKER] = preset_id
    tenant_store.set_params(username, stored)
    return True


@app.post("/api/strategy-params")
def strategy_params_set(payload: dict = Body(...), x_hl_token: str = Header(default="")):
    _check_internal_token(x_hl_token)
    username = _clean_username(payload.get("username"))
    updates = payload.get("updates")
    if not isinstance(updates, dict):
        raise HTTPException(422, "updates must be an object")
    rejected = _write_tenant_params(username, updates)
    return {"rejected": rejected, **strategy_params_get(username, INTERNAL_TOKEN)}


@app.get("/api/strategy-info")
def strategy_info(username: str, x_hl_token: str = Header(default="")):
    """動いている戦略の人間向け説明＋プリセット一覧＋現在のプリセット/実効設定。
    ダッシュボードの『どんな戦略でどう調整できるか』カード用。"""
    _check_internal_token(x_hl_token)
    username = _clean_username(username)
    tenant_store.get_or_create(username, hl_connector.generate_agent_wallet)
    stored = tenant_store.get_params(username)
    eff = hl_presets.effective_params(stored)
    return {
        "strategy": hl_presets.STRATEGY_INFO,
        "presets": hl_presets.presets_public(),
        "current_preset": hl_presets.infer_preset(stored),
        "effective": eff,
        "summary": {
            "max_open_trades": eff.get("max_open_trades"),
            "leverage": eff.get("leverage"),
            "is_long_enabled": eff.get("is_long_enabled"),
            "is_short_enabled": eff.get("is_short_enabled"),
            "enable_breakout_gate": eff.get("enable_breakout_gate"),
            "stoploss_pct": eff.get("stoploss_pct"),
            "ema_fast": eff.get("ema_fast"),
            "ema_slow": eff.get("ema_slow"),
        },
    }


@app.get("/api/fx-info")
def fx_info(x_hl_token: str = Header(default="")):
    """FXタブ用: FX戦略の説明・FX既定パラメータ・FXユニバース(表示名)。読み取りのみ。"""
    _check_internal_token(x_hl_token)
    p = hl_presets.FX_PRESET_PARAMS
    return {
        "strategy": hl_presets.FX_STRATEGY_INFO,
        "universe": [c.split(":", 1)[-1] for c in hl_loop.FX_UNIVERSE],
        "settings": {
            "max_open_trades": p["max_open_trades"], "leverage": p["leverage"],
            "is_long_enabled": p["is_long_enabled"], "is_short_enabled": p["is_short_enabled"],
            "enable_breakout_gate": p["enable_breakout_gate"], "stoploss_pct": p["stoploss_pct"],
            "peak_trail_trigger_pct": p["peak_trail_trigger_pct"],
            "ema_fast": p["ema_fast"], "ema_slow": p["ema_slow"],
        },
        "live_trading": False,  # FX自動売買は近日(現在はバックテスト+AI判断のみ)
    }


@app.get("/api/fx-judgment")
def fx_judgment(username: str, x_hl_token: str = Header(default=""),
                x_hl_payment_ref: str = Header(default="")):
    """FX市場のAI判断(kfxbrain)。admin=無料gemma / 一般=x402 DeepSeek。
    FXユニバースを判定し、有望(long/short)と見送り(veto)を返す。読み取りのみ。"""
    _check_internal_token(x_hl_token)
    username = _clean_username(username)
    if username != ADMIN_USERNAME and not x_hl_payment_ref:
        raise HTTPException(402, "payment required: X-HL-Payment-Ref header missing")
    provider = brain.provider_for(username, ADMIN_USERNAME)
    p = hl_loop._core_params(username)
    assets = []
    for c in hl_loop.FX_UNIVERSE:
        df = hl_loop.fetch_candles(c, "1h", 60)
        if df.empty:
            continue
        assets.append(brain.build_asset_evidence(c, strategy_core.populate_indicators(df, p), "fx"))
    if not assets:
        raise HTTPException(502, "FX価格データが取得できませんでした")
    try:
        gate = brain.market_gate("fx", assets, provider=provider)
    except Exception as exc:
        raise HTTPException(502, "kfxbrain judgment failed: %s" % str(exc)[:150])
    rows = [{"symbol": s, **g} for s, g in gate.items()]
    rows.sort(key=lambda r: (r.get("score") or 0), reverse=True)
    return {"provider": provider, "model": ("gemma4" if provider == "gemma" else "deepseek"),
            "rows": rows}


# ---------------------------------------------------------------------------
# ペーパーFX(実弾なしの仮想売買・テナント別)。資金/ウォレット委任は不要なので、
# アンバサダー(一般ユーザー)もXログインだけで開始できる。
# ---------------------------------------------------------------------------

@app.post("/api/paper-fx/start")
def paper_fx_start(payload: dict = Body(...), x_hl_token: str = Header(default="")):
    """ペーパーFX開始。一般ユーザーはkfxbrainをx402(DeepSeek)で使うため、支払い用
    ウォレットの接続(アドレス)が必須(取引の委任approveAgentは不要)。adminは無料
    gemmaなのでウォレット不要。"""
    _check_internal_token(x_hl_token)
    username = _clean_username(payload.get("username"))
    tenant_store.get_or_create(username, hl_connector.generate_agent_wallet)
    is_admin = username == ADMIN_USERNAME
    payer = (payload.get("payer_wallet") or "").strip()
    if not is_admin:
        if not payer.startswith("0x") or len(payer) != 42:
            raise HTTPException(422, "一般ユーザーはx402支払い用ウォレットの接続が必要です"
                                     "（取引の委任は不要・接続のみ）")
    acc = tenant_store.paper_enable(username, "fx", payer_wallet=(payer or None))
    return {"ok": True, "account": acc, "requires_wallet": (not is_admin)}


@app.post("/api/paper-fx/reset")
def paper_fx_reset(payload: dict = Body(...), x_hl_token: str = Header(default="")):
    _check_internal_token(x_hl_token)
    username = _clean_username(payload.get("username"))
    acc = tenant_store.paper_reset(username, "fx")
    return {"ok": True, "account": acc}


@app.get("/api/paper-fx/dashboard")
def paper_fx_dashboard(username: str, x_hl_token: str = Header(default="")):
    _check_internal_token(x_hl_token)
    username = _clean_username(username)
    import hl_paper_fx
    return hl_paper_fx.paper_dashboard(username, "fx")


@app.post("/api/paper-fx/run-cycle")
def paper_fx_run_cycle(payload: dict = Body(default={}), x_hl_token: str = Header(default="")):
    """管理用: ペーパーFXの1サイクルを手動で回す(毎時待たずに検証するため)。"""
    _check_internal_token(x_hl_token)
    import hl_paper_fx
    try:
        return hl_paper_fx.run_cycle()
    except Exception as exc:
        raise HTTPException(502, "paper-fx run-cycle failed: %s" % str(exc)[:200])


# ---- ペーパー現物(実弾なし・ロングのみ・レバ1倍・清算なし。kfreqaiとの比較用) ----
@app.post("/api/paper-spot/start")
def paper_spot_start(payload: dict = Body(...), x_hl_token: str = Header(default="")):
    """ペーパー現物開始。paper-fxと同じく、一般ユーザーはkcbrainをx402(DeepSeek)で
    使うため支払い用ウォレット接続が必須(取引の委任は不要)。adminは無料gemmaで不要。"""
    _check_internal_token(x_hl_token)
    username = _clean_username(payload.get("username"))
    tenant_store.get_or_create(username, hl_connector.generate_agent_wallet)
    is_admin = username == ADMIN_USERNAME
    payer = (payload.get("payer_wallet") or "").strip()
    if not is_admin:
        if not payer.startswith("0x") or len(payer) != 42:
            raise HTTPException(422, "一般ユーザーはx402支払い用ウォレットの接続が必要です"
                                     "（取引の委任は不要・接続のみ）")
    acc = tenant_store.paper_enable(username, "spot", payer_wallet=(payer or None))
    return {"ok": True, "account": acc, "requires_wallet": (not is_admin)}


@app.post("/api/paper-spot/reset")
def paper_spot_reset(payload: dict = Body(...), x_hl_token: str = Header(default="")):
    _check_internal_token(x_hl_token)
    username = _clean_username(payload.get("username"))
    acc = tenant_store.paper_reset(username, "spot")
    return {"ok": True, "account": acc}


@app.get("/api/paper-spot/dashboard")
def paper_spot_dashboard(username: str, x_hl_token: str = Header(default="")):
    _check_internal_token(x_hl_token)
    username = _clean_username(username)
    import hl_paper_spot
    return hl_paper_spot.paper_dashboard(username)


@app.post("/api/paper-spot/run-cycle")
def paper_spot_run_cycle(payload: dict = Body(default={}), x_hl_token: str = Header(default="")):
    """管理用: ペーパー現物の1サイクルを手動で回す(毎時待たずに検証)。"""
    _check_internal_token(x_hl_token)
    import hl_paper_spot
    try:
        return hl_paper_spot.run_cycle()
    except Exception as exc:
        raise HTTPException(502, "paper-spot run-cycle failed: %s" % str(exc)[:200])


# ---------------------------------------------------------------------------
# 2026-07-27: ペーパーMEXC先物のエンドポイントは撤去。MEXC先物はfreqtradeの
# 先物モード(kfreqai-futures-short :18343)へ一本化したため、この製品
# (kfreqaihl=Hyperliquid専用)からは扱わない。
# ---------------------------------------------------------------------------

@app.post("/api/apply-preset")
def apply_preset(payload: dict = Body(...), x_hl_token: str = Header(default="")):
    _check_internal_token(x_hl_token)
    username = _clean_username(payload.get("username"))
    preset_id = str(payload.get("preset") or "")
    if not _apply_preset(username, preset_id):
        raise HTTPException(422, "unknown preset")
    return {"ok": True, "current_preset": preset_id,
            **strategy_params_get(username, INTERNAL_TOKEN)}


# ---------------------------------------------------------------------------
# 戦略評価/実行(共通コアstrategy_coreを実Hyperliquidローソク足で走らせる)。
# decide=判断のみ(発注しない)。execute=モック安全な発注まで(HL_MOCK/LIVE次第)。
# ---------------------------------------------------------------------------

@app.get("/api/decide")
def decide(username: str, coin: str = hl_loop.DEFAULT_COIN,
           interval: str = hl_loop.DEFAULT_INTERVAL, x_hl_token: str = Header(default="")):
    _check_internal_token(x_hl_token)
    username = _clean_username(username)
    coin = "".join(ch for ch in coin if ch.isalnum())[:12] or hl_loop.DEFAULT_COIN
    try:
        return hl_loop.decide(username, coin, interval)
    except Exception as exc:
        raise HTTPException(502, "decide failed: %s" % str(exc)[:200])


@app.post("/api/execute")
def execute(payload: dict = Body(...), x_hl_token: str = Header(default="")):
    _check_internal_token(x_hl_token)
    username = _clean_username(payload.get("username"))
    coin = "".join(ch for ch in str(payload.get("coin") or hl_loop.DEFAULT_COIN) if ch.isalnum())[:12]
    interval = str(payload.get("interval") or hl_loop.DEFAULT_INTERVAL)
    try:
        return hl_loop.execute(username, coin or hl_loop.DEFAULT_COIN, interval)
    except Exception as exc:
        raise HTTPException(502, "execute failed: %s" % str(exc)[:200])


@app.post("/api/run-cycle")
def run_cycle(payload: dict = Body(default={}), x_hl_token: str = Header(default="")):
    """管理用: 常時稼働エンジンの1サイクルを手動で回す(1時間待たずに検証するため)。
    本番はhl_engine.py(systemd)が自動で回す。ここはトークン認証のみ。"""
    _check_internal_token(x_hl_token)
    import hl_engine
    try:
        return hl_engine.run_cycle(hl_loop.DEFAULT_INTERVAL)
    except Exception as exc:
        raise HTTPException(502, "run-cycle failed: %s" % str(exc)[:200])


@app.post("/api/backtest")
def backtest(payload: dict = Body(default={}), x_hl_token: str = Header(default="")):
    """本番と同じ共通コアを、実Hyperliquid履歴で再生するバックテスト。
    usernameを渡すとそのテナントの現在パラメータで、省略時は既定値で走る。"""
    _check_internal_token(x_hl_token)
    import hl_backtest
    username = payload.get("username")
    username = _clean_username(username) if username else None
    try:
        days = int(payload.get("days") or 30)
    except (TypeError, ValueError):
        days = 30
    days = max(3, min(days, 180))  # 過大要求で取得が重くなり過ぎないよう上限
    market = str(payload.get("market") or "crypto").lower()
    try:
        if market == "fx":
            r = hl_backtest.run_fx_backtest(days=days)
        else:
            r = hl_backtest.run_backtest(username=username, days=days,
                                         interval=str(payload.get("interval") or "1h"))
    except Exception as exc:
        raise HTTPException(502, "backtest failed: %s" % str(exc)[:200])
    r["summary_ja"] = hl_backtest.summarize_ja(r)
    return r


# ---------------------------------------------------------------------------
# チャット(バイブトレーディングUI)。xb_bittensorはgemma4無料、それ以外は
# DeepSeek+x402課金(Phase1は支払いヘッダーの存在チェックのみ=スタブ)。
# ---------------------------------------------------------------------------

PERSONA_PROMPT = """あなたは「Kurageさん」。Hyperliquid上のパーペチュアル取引botと一緒に
暮らすAI VTuberで、ユーザーの「トレードの相棒」です。できることは3つ:
(1) ユーザーの質問(取引状況・残高・保有ポジション・損益など)に、下記「今の口座状況」
    の事実だけを使って、親しみやすく正直に答える。データに無いことは
    「そこまでは分からない」と正直に言う。
(2) ユーザーの要望を、決まったパラメータの範囲内での変更(param_change)に変換する。
    任意のコードや新しいロジックは作らない(バイブトレーディング)。
(3) 「バックテストして」と言われたら、今の設定を実際の過去相場で再生して成績を返す
    (この処理はシステム側が自動で行うので、あなたは案内するだけでよい)。
(4) ユーザーが戦略の雰囲気を変えたいとき(「積極的に」「安全に」など)は、下記の
    プリセットへの切り替え(preset_change)に変換する。
(5) 「BTCどう思う？」等の相場・銘柄の判断は、kcbrain(crypto)/kfxbrain(FX)のAIが
    答えます(システムが自動で処理するので、あなたは案内するだけでよい)。

# 動いている戦略(共通・変わらない)
トレンド追随(EMAクロス)戦略。短期/長期EMAのクロスで乗り、RSI過熱やピーク押し戻しで決済。

# 選べるプリセット(preset_changeのidはこの中から)
{presets_desc}

# 今の口座状況(自動取得された事実。ここにある数字だけを使う)
{live_context}

# 変更してよいパラメータ(この名前と範囲を厳守。他は一切変更しない)
{schema_desc}

# 出力形式(厳守: このJSON以外を一切出力しない)
{{"reply": "ユーザーへの返事(3〜5文、親しみやすく正直に)",
 "action": null
   または {{"type": "param_change", "param": "上のキーのどれか", "value": 数値またはtrue/false}}
   または {{"type": "preset_change", "preset": "上のプリセットidのどれか"}}}}

質問に答えるだけのとき(取引状況など)はactionをnullにする。1つの数値だけ変えたい
ときはparam_change、戦略全体の雰囲気(堅実/積極/短期回転など)を変えたいときは
preset_changeを使う。パラメータ一覧で表現できない要望(新しい指標など)は正直に
「今のわたしにはできない、運営に相談してください」と伝え、actionはnull。
"""


def _build_live_context(username):
    """そのユーザーのHyperliquid口座の実況を日本語テキストで作る(チャットに渡す事実)。
    失敗しても会話は止めない。"""
    try:
        tenant = tenant_store.get_or_create(username, hl_connector.generate_agent_wallet)
        if not tenant.get("main_wallet_address"):
            return "(まだメイン口座が未登録。取引データなし)"
        d = hl_connector.get_dashboard(tenant["main_wallet_address"])
        if d.get("mock"):
            return "(シミュレーション(モック)モード。実際の取引データではありません)"
        lines = []
        lines.append(f"総資産: {d.get('account_value_usd', 0):.2f} USDC "
                     f"(含み損益 {d.get('unrealized_pnl_usd', 0):+.2f} / 確定損益累計 "
                     f"{d.get('closed_pnl_total_usd', 0):+.2f})")
        pos = d.get("positions") or []
        if pos:
            lines.append(f"保有中ポジション {len(pos)}件:")
            for p in pos[:15]:
                side = "ショート" if p["is_short"] else "ロング"
                lines.append(f"  {p['coin']} {side} サイズ{p['size']} 建値{p['entry_px']} "
                             f"含み{p['unrealized_pnl_usd']:+.2f}USDC")
        else:
            lines.append("保有中ポジション: なし")
        fills = d.get("fills") or []
        if fills:
            lines.append(f"直近の約定{min(len(fills),5)}件: " + " / ".join(
                f"{f['coin']}{'売' if f['side']=='sell' else '買'}" for f in fills[:5]))
        p = hl_loop._core_params(username)
        lines.append(f"設定: 枠{p.get('max_open_trades')} レバ{p.get('leverage')}倍 "
                     f"ロング{'有' if p.get('is_long_enabled') else '無'}/"
                     f"ショート{'有' if p.get('is_short_enabled') else '無'}")
        return "\n".join(lines)
    except Exception as exc:
        return f"(口座状況の取得に失敗: {str(exc)[:80]})"


def _schema_desc(schema):
    lines = []
    for s in schema:
        rng = f"{s['min']}〜{s['max']}" if s["type"] in ("int", "float") else "true/false"
        lines.append(f"- {s['key']} ({s['label']['ja']}): {rng} (現在の既定値 {s['default']})")
    return "\n".join(lines)


def _presets_desc():
    return "\n".join(f"- id={p['id']} ({p['name']}): {p['desc']}" for p in hl_presets.PRESETS)


def _extract_json(text):
    from lab_common import extract_json
    return extract_json(text)


def _is_backtest_request(message):
    m = (message or "").lower()
    return ("バックテスト" in message or "backtest" in m or "back test" in m
            or "過去検証" in message or "検証して" in message)


def _parse_backtest_days(message, default=30):
    """『90日でバックテスト』『過去3ヶ月』などから日数を推定。既定30日。"""
    import re
    m = re.search(r"(\d+)\s*(日|days?|day)", message, re.IGNORECASE)
    if m:
        return max(3, min(int(m.group(1)), 180))
    m = re.search(r"(\d+)\s*(ヶ月|ケ月|か月|カ月|months?|month)", message, re.IGNORECASE)
    if m:
        return max(3, min(int(m.group(1)) * 30, 180))
    return default


_JUDGE_KEYWORDS = ("どう思う", "どう思い", "判断", "見て", "買っていい", "売っていい",
                   "買い時", "売り時", "エントリー", "おすすめ", "有望", "チャンス",
                   "相場", "見通し", "強い銘柄", "opportunity")
_FX_BASES = {b.split(":", 1)[-1].upper().lstrip("k") for b in hl_loop.FX_UNIVERSE}
_CRYPTO_BASES = {c.upper() for c in hl_loop.DEFAULT_UNIVERSE}


def _is_judgment_request(message):
    return any(k in message for k in _JUDGE_KEYWORDS)


def _detect_symbol(message):
    """メッセージから銘柄を推定。(coin, market) か (None, None)。FX優先で照合。"""
    up = message.upper()
    for b in sorted(_FX_BASES, key=len, reverse=True):
        if b in up:
            for full in hl_loop.FX_UNIVERSE:
                if full.split(":", 1)[-1].upper().lstrip("K") == b:
                    return full, "fx"
    for c in sorted(_CRYPTO_BASES, key=len, reverse=True):
        if c in up:
            return c, "crypto"
    return None, None


def _run_chat_judgment(username, message):
    """kcbrain(crypto)/kfxbrain(FX)にこのユーザーのproviderで市場判断を仰ぐ。
    admin=無料gemma / 一般=x402 DeepSeek。銘柄指定があればその1件、無ければ市場全体の
    機会ランキング上位を返す。数字・判断はbrainの結果のみ(捏造しない)。"""
    provider = brain.provider_for(username, ADMIN_USERNAME)
    coin, market = _detect_symbol(message)
    p = hl_loop._core_params(username)
    try:
        if coin:  # 1銘柄
            df = hl_loop.fetch_candles(coin, "1h", 60)
            if df.empty:
                return {"reply": f"{coin}の価格データが取れませんでした。", "applied": None, "model": "brain"}
            df = strategy_core.populate_indicators(df, p)
            assets = [brain.build_asset_evidence(coin, df, market)]
            gate = brain.market_gate(market, assets, provider=provider)
            g = gate.get(brain._base(coin)) or {}
            name = "kfxbrain" if market == "fx" else "kcbrain"
            disp = coin.split(":", 1)[-1]
            if not g:
                reply = f"{disp}について{name}から明確な判断が返りませんでした。しばらくして再度お試しください。"
            else:
                dir_ja = {"long": "ロング(買い)有望", "short": "ショート(売り)有望",
                          "watch": "様子見", "avoid": "見送り推奨"}.get(g.get("direction"), g.get("direction"))
                reply = (f"{name}の判断（{disp}）：{dir_ja}。"
                         f"スコア{g.get('score')}/信頼度{g.get('confidence')}。理由：{g.get('why')}。"
                         + ("\n※これはAIの参考判断です。最終判断はご自身で。" ))
        else:  # 市場全体(crypto既定)
            market = "crypto"
            assets = []
            for c in hl_loop.DEFAULT_UNIVERSE[:20]:
                df = hl_loop.fetch_candles(c, "1h", 60)
                if df.empty:
                    continue
                assets.append(brain.build_asset_evidence(c, strategy_core.populate_indicators(df, p), market))
            gate = brain.market_gate(market, assets, provider=provider)
            longs = [(s, g) for s, g in gate.items() if g.get("direction") == "long"]
            longs.sort(key=lambda x: (x[1].get("score") or 0), reverse=True)
            if longs:
                top = "、".join(f"{s}(スコア{g.get('score')})" for s, g in longs[:5])
                reply = f"kcbrainの市場判断：今ロング有望なのは {top} です。\n※AIの参考判断です。"
            else:
                reply = "kcbrainの市場判断：今は明確にロング有望な銘柄は見当たりません（様子見多め）。\n※AIの参考判断です。"
    except Exception as exc:
        return {"reply": f"ごめんなさい、AI判断の取得中にエラーが出ました（{str(exc)[:120]}）。",
                "applied": None, "model": "brain"}
    return {"reply": reply, "applied": None, "model": ("gemma4-local" if provider == "gemma" else "deepseek-x402")}


def _run_chat_backtest(username, message):
    """チャットからのバックテスト。そのユーザーの現在パラメータで実履歴を再生し、
    Kurageさん風の日本語サマリを返す(数字は実結果のみ・捏造しない)。"""
    import hl_backtest
    days = _parse_backtest_days(message)
    try:
        r = hl_backtest.run_backtest(username=username, days=days, interval="1h")
    except Exception as exc:
        return {"reply": f"ごめんなさい、バックテストの実行中にエラーが出ました({str(exc)[:120]})。",
                "applied": None, "model": "backtest", "backtest": {"ok": False}}
    return {"reply": hl_backtest.summarize_ja(r), "applied": None,
            "model": "backtest", "backtest": r}


@app.post("/api/chat")
def chat(payload: dict = Body(...), x_hl_token: str = Header(default=""),
          x_hl_payment_ref: str = Header(default="")):
    _check_internal_token(x_hl_token)
    username = _clean_username(payload.get("username"))
    message = (payload.get("message") or "").strip()
    if not message:
        raise HTTPException(422, "message is required")
    if len(message) > MAX_MESSAGE_CHARS:
        raise HTTPException(422, f"message too long (max {MAX_MESSAGE_CHARS})")

    is_admin = username == ADMIN_USERNAME
    if not is_admin:
        # TODO(Phase2): x402の実オンチェーン決済検証に置き換える
        # (url2ai/apps/llm-gateway/server-jpyc-url2brain.js と同じゲートウェイ方式)。
        # 現状は支払い証明ヘッダーの有無しか見ておらず、実際の決済確認をしていない。
        if not x_hl_payment_ref:
            raise HTTPException(402, "payment required: X-HL-Payment-Ref header missing")

    # バックテスト要求は、LLMに数字を作らせず、実履歴で決定論的に走らせて返す
    # (「45日でバックテスト」等の日数指定も拾う)。本番と同じ共通コアを使うので
    # 「今の設定を過去で試す」という意味が保たれる。
    if _is_backtest_request(message):
        return _run_chat_backtest(username, message)

    # 相場・銘柄の判断はkcbrain(crypto)/kfxbrain(FX)に仰ぐ。admin=無料gemma /
    # 一般=x402 DeepSeek(上のx402ゲートを既に通過している)。パラメータ変更の要望は
    # 含まないので、判断キーワードのときだけここで処理する。
    if _is_judgment_request(message):
        return _run_chat_judgment(username, message)

    schema = hl_schemas.SCHEMAS[hl_schemas.DEFAULT_STRATEGY]
    prompt = (PERSONA_PROMPT.format(schema_desc=_schema_desc(schema),
                                    presets_desc=_presets_desc(),
                                    live_context=_build_live_context(username))
              + f"\n\nユーザー: {message}\n\nKurageさんのJSON出力:")

    if is_admin:
        raw = llm_client.call_gemma(prompt, num_predict=400, temperature=0.4, timeout=180)
        model_used = "gemma4-local"
    else:
        raw = deepseek_client.chat(prompt, temperature=0.4, max_tokens=400)
        model_used = "deepseek"

    parsed = _extract_json(raw)
    if not (isinstance(parsed, dict) and parsed.get("reply")):
        parsed = {"reply": raw.strip()[:500], "action": None}

    reply = str(parsed.get("reply") or "")[:1000]
    action = parsed.get("action")
    applied = None
    if isinstance(action, dict) and action.get("type") == "param_change":
        rejected = _write_tenant_params(username, {action["param"]: action.get("value")})
        if action["param"] in rejected:
            reply += f"\n(ごめんなさい、{action['param']}の値を反映できませんでした)"
        else:
            applied = {action["param"]: action.get("value")}
    elif isinstance(action, dict) and action.get("type") == "preset_change":
        preset_id = str(action.get("preset") or "")
        if _apply_preset(username, preset_id):
            preset = hl_presets.get_preset(preset_id)
            applied = {"preset": preset_id}
            reply += f"\n（戦略を「{preset['name']}」に切り替えました）"
        else:
            reply += "\n（ごめんなさい、そのプリセットは見つかりませんでした）"

    return {"reply": reply, "applied": applied, "model": model_used,
            "current_preset": _current_preset(username)}
