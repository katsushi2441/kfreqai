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
import re
import threading

import requests
from fastapi import Body, FastAPI, Header, HTTPException

import backtest_jobs
import facts_collect
import judgment_backend
import llm_client
import market_facts
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
# 取引前チェック(x402外販の主力。kfreqai自身が毎日使っている診断の外部提供)
# ---------------------------------------------------------------------------

def _valid_symbol(payload):
    symbol = str(payload.get("symbol") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{2,15}", symbol):
        raise HTTPException(422, "symbol must be 2-15 alphanumeric chars, e.g. BTC")
    return symbol


@app.post("/v1/trade/risk-check")
def trade_risk_check(payload: dict = Body(...)):
    """銘柄の直近ネガティブイベント検査(hack/exploit/delisting/rug_pull/lawsuit)。

    market_factsのキャッシュにあればそれを返し、無ければその場でニュースを
    収集・gemma4分類してDBにも蓄積する(呼ばれるほどキャッシュが厚くなる)。"""
    symbol = _valid_symbol(payload)
    conn = market_facts.connect()
    facts = market_facts.valid_facts(conn, symbol, limit=8)
    fetched_now = False
    if not facts:
        try:
            items = facts_collect.fetch_news_rss(symbol)[:5]
        except Exception as exc:
            raise HTTPException(502, f"news fetch failed: {str(exc)[:100]}")
        with _LLM_LOCK:
            for art in items:
                try:
                    sigs = judgment_backend.classify_news(art, [symbol])
                except Exception:
                    continue
                for sig in sigs:
                    if str(sig.get("symbol", "")).upper() != symbol:
                        continue
                    ev = sig.get("event_type")
                    neg = (sig.get("sentiment") == "negative"
                           and ev in judgment_backend.NEWS_NEGATIVE_EVENTS
                           and float(sig.get("confidence") or 0)
                           >= judgment_backend.NEWS_BLOCK_CONFIDENCE_THRESHOLD)
                    market_facts.add_fact(
                        conn, symbol,
                        fact=f"{art['title'][:140]} [{sig.get('sentiment')}/{ev}]",
                        event_type=ev, sentiment=sig.get("sentiment"),
                        confidence=sig.get("confidence"), source_url=art["link"],
                        ttl_hours=(facts_collect.NEGATIVE_TTL_H if neg
                                   else facts_collect.DEFAULT_TTL_H),
                        raw_title=art["title"])
        fetched_now = True
        facts = market_facts.valid_facts(conn, symbol, limit=8)
    events = [{"event_type": f.get("event_type"), "sentiment": f.get("sentiment"),
               "confidence": f.get("confidence"), "fact": f.get("fact"),
               "source_url": f.get("source_url"), "observed_at": f.get("observed_at"),
               "expires_at": f.get("expires_at")} for f in facts]
    negative = [e for e in events
                if e["sentiment"] == "negative"
                and e["event_type"] in judgment_backend.NEWS_NEGATIVE_EVENTS
                and (e["confidence"] or 0) >= judgment_backend.NEWS_BLOCK_CONFIDENCE_THRESHOLD]
    return {"symbol": symbol,
            "verdict": "block" if negative else "ok",
            "negative_events": negative, "events": events,
            "source": "fresh_scan" if fetched_now else "recent_cache",
            "note": ("直近に高確度のネガティブイベントあり。24時間はエントリー見送りを推奨"
                     if negative else
                     ("直近のニュースに危険イベントは見つからなかった" if events
                      else "直近のニュースが見つからなかった(無風≠安全の点は留意)"))}


@app.post("/v1/trade/size-check")
def trade_size_check(payload: dict = Body(...)):
    """注文サイズの流動性診断(LiqCapの外販)。LLM不使用・高速。

    kfreqai実運用ルール: 1注文は24h出来高の0.1%まで。実測では板の薄い銘柄の
    成行損切りで平均-0.58pt/最悪-1.72ptの滑り(ギャップ主導)が出ている。"""
    symbol = _valid_symbol(payload)
    try:
        size = float(payload.get("order_size_usdt"))
    except (TypeError, ValueError):
        raise HTTPException(422, "order_size_usdt must be a number")
    if not 0 < size < 1e9:
        raise HTTPException(422, "order_size_usdt out of range")
    try:
        r = requests.get("https://api.mexc.com/api/v3/ticker/24hr",
                         params={"symbol": f"{symbol}USDT"}, timeout=10)
    except Exception as exc:
        raise HTTPException(502, f"exchange data fetch failed: {str(exc)[:100]}")
    if r.status_code != 200:
        raise HTTPException(404, f"{symbol}/USDT not found on MEXC spot")
    try:
        qv = float(r.json().get("quoteVolume") or 0)
    except (TypeError, ValueError):
        qv = 0.0
    if qv <= 0:
        raise HTTPException(502, "exchange returned no volume data")
    liq_cap_pct = 0.001  # kfreqai LiqCap実運用値: 24h出来高の0.1%
    cap = qv * liq_cap_pct
    thin = qv < 1_000_000
    verdict = "ok" if size <= cap else "too_large"
    return {"symbol": symbol, "pair": f"{symbol}/USDT",
            "quote_volume_24h_usdt": round(qv, 2),
            "order_size_usdt": size,
            "volume_share_pct": round(100 * size / qv, 4),
            "max_safe_size_usdt": round(cap, 2),
            "liq_cap_pct": liq_cap_pct * 100,
            "thin_market": thin, "verdict": verdict,
            "note": ("サイズは許容範囲内(24h出来高の0.1%ルール)" if verdict == "ok"
                     else f"大きすぎる。この銘柄の推奨上限は{cap:,.0f} USDT")
                    + ("。24h出来高100万USDT未満の薄い市場なので、成行時の滑りに特に注意"
                       if thin else "")}


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
