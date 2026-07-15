"""judgment_api — kfreqaiのLLM判断機能をRESTで公開するFastAPIサービス。

目的: LLM環境(GPU/Ollama/Claude CLI)を持たないユーザーでも、kfreqai風の
自作botからHTTPだけで「相場判断」を使えるようにする。将来はこの前段に
x402課金ゲートウェイ(url2aiのllm-gatewayと同じ構成)を置いて有料公開する。

エンジン切替(重要):
  各POSTのボディに "engine": "auto" | "gemma" を渡せる(既定はauto)。
  - auto : judgment_backendの実装そのまま(claude -> codex -> gemma4の3段)。
           ローカル無課金利用向け。
  - gemma: gemma4(Ollama)のみを使う。x402ゲートウェイは必ずこれを指定する
           ことで、個人サブスクのClaude CLIが有料経路に乗らない(規約対策)。
  ヘッダ X-Judgment-Engine でも指定可(ボディが優先)。

同時実行: LLMは単一GPUのOllama/単一のCLIなので、プロセス内ロックで
直列化する。重い呼び出しが重なると待たされるのは仕様。

入出力スキーマの詳細と curl 例は JUDGMENT_API.md を参照。
起動: uvicorn judgment_api:app --host 127.0.0.1 --port 18321
"""
import threading

from fastapi import Body, FastAPI, Header, HTTPException

import backtest_jobs
import judgment_backend
import llm_client
import research_daily
from lab_common import extract_json

# gemma強制エンジン用にプロンプトビルダーを直接使う(private実装がある場合のみ)
try:
    import judgment_logic as _logic
except ImportError:
    _logic = None

app = FastAPI(title="kfreqai judgment API", version="1.1")
backtest_jobs.start()

# LLM呼び出しの直列化(GPU1本 + CLI同時実行防止)
_LLM_LOCK = threading.Lock()

VALID_ENGINES = {"auto", "gemma"}


def _pick_engine(payload, header_value):
    engine = (payload.get("engine") or header_value or "auto").strip().lower()
    if engine not in VALID_ENGINES:
        raise HTTPException(422, f"engine must be one of {sorted(VALID_ENGINES)}")
    if engine == "gemma" and _logic is None:
        # private実装なしのチェックアウトではgemma専用プロンプトが組めない
        raise HTTPException(503, "gemma engine unavailable on this deployment "
                                 "(judgment_logic not installed)")
    return engine


def _require(payload, key, typ, what):
    val = payload.get(key)
    if not isinstance(val, typ):
        raise HTTPException(422, f"'{key}' must be {what}")
    return val


@app.get("/v1/health")
def health():
    ollama_ok = False
    try:
        import requests
        r = requests.get(f"{llm_client.OLLAMA_URL}/api/tags", timeout=3)
        ollama_ok = r.status_code == 200
    except Exception:
        pass
    return {
        "ok": True,
        "backend": judgment_backend.BACKEND_NAME,
        "gemma_engine_available": _logic is not None,
        "ollama_reachable": ollama_ok,
        "ollama_model": llm_client.OLLAMA_MODEL,
    }


@app.get("/v1/meta")
def meta():
    """外部クライアントがスキーマ・分類カテゴリ・仮説DSLを発見するための情報。"""
    return {
        "news_event_types": judgment_backend.NEWS_EVENT_TYPES,
        "news_negative_events": sorted(judgment_backend.NEWS_NEGATIVE_EVENTS),
        "news_block_confidence_threshold": judgment_backend.NEWS_BLOCK_CONFIDENCE_THRESHOLD,
        "postmortem_categories": getattr(_logic, "TRADE_POSTMORTEM_CATEGORIES", None),
        "research_dsl": {
            "features": sorted(judgment_backend.RESEARCH_FEATURES),
            "ops": sorted(judgment_backend.RESEARCH_OPS),
            "params": {k: list(v) for k, v in judgment_backend.RESEARCH_PARAMS.items()},
        },
        "engines": sorted(VALID_ENGINES),
        "ohlcv_format": "pair -> list of [timestamp_ms, open, high, low, close, volume],"
                        " 1h candles, 30+ candles recommended",
    }


# ---------------------------------------------------------------------------
# 判断系
# ---------------------------------------------------------------------------

@app.post("/v1/judge/regime")
def judge_regime(payload: dict = Body(...)):
    ohlcv = _require(payload, "ohlcv_by_pair", dict, "a dict of pair -> candles")
    with _LLM_LOCK:
        regime, note, model = judgment_backend.classify_regime(ohlcv)
    return {"regime": regime, "note": note, "model": model}


@app.post("/v1/judge/directive")
def judge_directive(payload: dict = Body(...),
                    x_judgment_engine: str = Header(default="")):
    ohlcv = _require(payload, "ohlcv_by_pair", dict, "a dict of pair -> candles")
    history = payload.get("history") or []
    engine = _pick_engine(payload, x_judgment_engine)
    with _LLM_LOCK:
        if engine == "gemma":
            stats_block = _logic.build_stats_block(ohlcv)
            prompt = _logic._build_directive_prompt(stats_block, history)
            text = llm_client.call_gemma(prompt, num_predict=200, temperature=0.2)
            directive, note = _logic._parse_directive_response(text)
            model = llm_client.OLLAMA_MODEL
        else:
            directive, note, model = judgment_backend.decide_directive(ohlcv, history)
    return {"directive": directive, "note": note, "model": model}


@app.post("/v1/judge/news")
def judge_news(payload: dict = Body(...)):
    article = _require(payload, "article", dict, "a dict with title/summary")
    symbols = _require(payload, "symbols", list, "a list of symbols")
    if not article.get("title"):
        raise HTTPException(422, "'article.title' is required")
    article.setdefault("summary", "")
    with _LLM_LOCK:
        signals = judgment_backend.classify_news(article, symbols)
    return {
        "signals": signals,
        "negative_events": sorted(judgment_backend.NEWS_NEGATIVE_EVENTS),
        "block_confidence_threshold": judgment_backend.NEWS_BLOCK_CONFIDENCE_THRESHOLD,
    }


@app.post("/v1/judge/postmortem")
def judge_postmortem(payload: dict = Body(...)):
    ctx = _require(payload, "ctx", dict, "a dict (see trade_postmortem context)")
    # ルールフォールバックがexit_reasonだけtrade側から読むので補完しておく
    trade = payload.get("trade") or {"exit_reason": ctx.get("exit_reason")}
    with _LLM_LOCK:
        verdict = judgment_backend.review_trade(trade, ctx)
    return verdict


# ---------------------------------------------------------------------------
# 研究系
# ---------------------------------------------------------------------------

@app.post("/v1/research/hypotheses")
def research_hypotheses(payload: dict = Body(...),
                        x_judgment_engine: str = Header(default="")):
    dossier = _require(payload, "dossier", dict, "a dict of performance evidence")
    engine = _pick_engine(payload, x_judgment_engine)
    with _LLM_LOCK:
        if engine == "gemma":
            prompt = _logic._build_hypothesis_prompt(dossier)
            raw = llm_client.call_gemma(prompt, num_predict=600, temperature=0.2,
                                        timeout=300)
            parsed = extract_json(raw)
            if not isinstance(parsed, dict):
                raise HTTPException(502, "gemma4 output was not parseable JSON")
            hypotheses = parsed.get("hypotheses") or []
            model = llm_client.OLLAMA_MODEL
        else:
            hypotheses = judgment_backend.propose_hypotheses(dossier)
            model = "backend-default"
    return {"hypotheses": hypotheses, "model": model}


@app.post("/v1/research/variant-code")
def research_variant_code(payload: dict = Body(...)):
    """仮説JSON -> freqtrade検証用variant戦略コード。決定的な生成でLLMは使わない。"""
    h = _require(payload, "hypothesis", dict, "a hypothesis dict (see /v1/meta research_dsl)")
    kind = h.get("kind")
    if kind not in ("entry_filter", "param_change"):
        raise HTTPException(422, "hypothesis.kind must be entry_filter or param_change")
    if kind == "entry_filter":
        for c in h.get("conditions") or []:
            if c.get("feature") not in judgment_backend.RESEARCH_FEATURES:
                raise HTTPException(422, f"unknown feature {c.get('feature')!r}")
            if c.get("op") not in judgment_backend.RESEARCH_OPS:
                raise HTTPException(422, f"unknown op {c.get('op')!r}")
    if kind == "param_change" and h.get("param") not in judgment_backend.RESEARCH_PARAMS:
        raise HTTPException(422, f"unknown param {h.get('param')!r}")
    try:
        code = judgment_backend.build_variant_strategy_code(h)
    except Exception as exc:
        raise HTTPException(422, f"could not build strategy code: {exc}")
    return {"code": code, "class_name": "KfreqaiLabHypo",
            "filename_hint": "kfreqai_lab_hypo.py"}


# ---------------------------------------------------------------------------
# バックテスト(非同期ジョブ)
# ---------------------------------------------------------------------------

@app.post("/v1/backtest")
def backtest_submit(payload: dict = Body(...)):
    """仮説DSLを主要13銘柄でバックテストするジョブを積む。結果はjob_idでポーリング。"""
    h = _require(payload, "hypothesis", dict, "a hypothesis dict (see /v1/meta research_dsl)")
    research_daily.sanitize_name(h, 0)
    research_daily.normalize_units(h)
    err = research_daily.validate(h)
    if err:
        raise HTTPException(422, f"invalid hypothesis: {err}")
    timerange = payload.get("timerange") or backtest_jobs.default_timerange()
    tr_err = backtest_jobs.validate_timerange(timerange)
    if tr_err:
        raise HTTPException(422, tr_err)
    try:
        job = backtest_jobs.submit(h, timerange)
    except RuntimeError as exc:
        raise HTTPException(429, str(exc))
    return {"job_id": job["id"], "status": job["status"], "timerange": timerange,
            "hypothesis": h,
            "note": "poll GET /v1/backtest/{job_id}; first run per timerange trains "
                    "models and can take tens of minutes, later runs reuse the cache"}


@app.get("/v1/backtest/{job_id}")
def backtest_status(job_id: str):
    job = backtest_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, "unknown job_id")
    return job


@app.get("/v1/backtest")
def backtest_list():
    return {"jobs": backtest_jobs.list_jobs()}


# ---------------------------------------------------------------------------
# 執筆系
# ---------------------------------------------------------------------------

@app.post("/v1/write/status-article")
def write_status_article(payload: dict = Body(...),
                         x_judgment_engine: str = Header(default="")):
    ohlcv = _require(payload, "ohlcv_by_pair", dict, "a dict of pair -> candles")
    prev_value = payload.get("prev_value") or ""
    new_value = payload.get("new_value") or ""
    note = payload.get("note") or ""
    engine = _pick_engine(payload, x_judgment_engine)
    with _LLM_LOCK:
        if engine == "gemma":
            stats_block = _logic.build_stats_block(ohlcv)
            prompt = _logic._build_status_change_prompt(prev_value, new_value, note, stats_block)
            text = llm_client.call_gemma(prompt, num_predict=800, temperature=0.4,
                                         timeout=300)
            title, slug, body = _logic._parse_blog_response(text)
            article = {"title": title, "slug": slug, "body": body}
        else:
            article = judgment_backend.write_status_change_article(
                prev_value, new_value, note, ohlcv)
    return article


@app.post("/v1/write/blog-article")
def write_blog_article(payload: dict = Body(...),
                       x_judgment_engine: str = Header(default="")):
    ctx = _require(payload, "ctx", dict, "a dict (see blog_post.py context)")
    is_daily_summary = bool(payload.get("is_daily_summary"))
    seo_topic = payload.get("seo_topic")
    engine = _pick_engine(payload, x_judgment_engine)
    with _LLM_LOCK:
        if engine == "gemma":
            prompt = _logic._build_blog_prompt(ctx, is_daily_summary, seo_topic)
            text = llm_client.call_gemma(prompt, num_predict=1800, temperature=0.55,
                                         timeout=300)
            title, slug, body = _logic._parse_blog_response(text)
            article = {"title": title, "slug": slug, "body": body}
        else:
            article = judgment_backend.write_blog_article(ctx, is_daily_summary, seo_topic)
    return article


@app.post("/v1/write/satellite-article")
def write_satellite_article(payload: dict = Body(...)):
    channel = payload.get("channel")
    if channel not in ("hatena", "blogger"):
        raise HTTPException(422, "'channel' must be hatena or blogger")
    title = _require(payload, "title", str, "the source article title")
    permalink = _require(payload, "permalink", str, "the source article URL")
    body_markdown = _require(payload, "body_markdown", str, "the source article body")
    with _LLM_LOCK:
        article = judgment_backend.write_satellite_article(
            channel, title, permalink, body_markdown)
    if article is None:
        # 元実装の品質ゲート(長さ・URL混入・dry-run言及)に落ちた
        raise HTTPException(502, "generated article failed quality gate; retry")
    return article
