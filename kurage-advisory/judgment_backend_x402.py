"""x402 backend -- HTTP client for a remote kfreqai judgment API.

The "metered brain": this checkout stays fully public/OSS, while the real
judgment implementation (prompts, thresholds, live strategy parameters)
runs behind a judgment API deployment (judgment_api.py) somewhere else.
Point this client at it and every advisory loop in this repo works with
no LLM, no GPU and no API keys of your own:

    KFREQAI_JUDGMENT_BACKEND=x402
    KFREQAI_JUDGMENT_API_URL=https://<judgment-api-host>   # required

Optional:
    KFREQAI_JUDGMENT_ENGINE=gemma   # engine sent per request (default gemma;
                                    # paid gateways enforce gemma server-side)
    KFREQAI_JUDGMENT_TIMEOUT=330    # per-call timeout in seconds

Payment: a paid deployment sits behind an x402 gateway (Bankr / JPYC / CDP,
see the llm2api /trade/* skills for the same pattern). If the endpoint
answers 402 Payment Required, this client raises with a hint instead of
retrying -- payment negotiation/signing is the job of your x402-aware
proxy or wallet layer, not of this module.

Fail-open contract: like the local_llm backend, transient failures fall
back to judgment_backend_rule so scheduled runs degrade instead of crash.
propose_hypotheses/build_variant_strategy_code do NOT fall back silently
(a rule-based "empty" answer there is useless) -- they raise.
"""
import json
import os

import requests

import judgment_backend_rule as _rule_fallback

API_URL = os.environ.get("KFREQAI_JUDGMENT_API_URL", "").rstrip("/")
ENGINE = os.environ.get("KFREQAI_JUDGMENT_ENGINE", "gemma").strip().lower()
TIMEOUT = int(os.environ.get("KFREQAI_JUDGMENT_TIMEOUT", "330"))

MODEL_LABEL = "x402-remote"


class X402ConfigError(RuntimeError):
    pass


class X402PaymentRequired(RuntimeError):
    pass


def _post(path, payload, timeout=None):
    if not API_URL:
        raise X402ConfigError(
            "KFREQAI_JUDGMENT_API_URL is not set. Point it at a judgment API "
            "deployment (see kurage-advisory/JUDGMENT_API.md).")
    payload = dict(payload)
    payload.setdefault("engine", ENGINE)
    resp = requests.post(API_URL + path, json=payload, timeout=timeout or TIMEOUT,
                         headers={"Content-Type": "application/json"})
    if resp.status_code == 402:
        raise X402PaymentRequired(
            f"{path}: endpoint requires x402 payment; route this client through "
            "your x402-aware proxy/wallet, or use an unmetered deployment")
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# meta (classification categories + research DSL), fetched once with fallback
# ---------------------------------------------------------------------------

_META_FALLBACK = {
    "news_event_types": ["listing", "partnership", "upgrade", "hack", "exploit",
                          "delisting", "rug_pull", "lawsuit", "regulatory", "other"],
    "news_negative_events": ["hack", "exploit", "delisting", "rug_pull", "lawsuit"],
    "news_block_confidence_threshold": 0.6,
    "research_dsl": {"features": [], "ops": [], "params": {}},
}


def _fetch_meta():
    if not API_URL:
        return _META_FALLBACK
    try:
        resp = requests.get(API_URL + "/v1/meta", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        print(f"[judgment_x402] /v1/meta unavailable, using fallback constants: "
              f"{str(exc)[:80]}", flush=True)
        return _META_FALLBACK


_meta = _fetch_meta()

NEWS_EVENT_TYPES = list(_meta.get("news_event_types") or _META_FALLBACK["news_event_types"])
NEWS_NEGATIVE_EVENTS = set(_meta.get("news_negative_events")
                           or _META_FALLBACK["news_negative_events"])
NEWS_BLOCK_CONFIDENCE_THRESHOLD = float(
    _meta.get("news_block_confidence_threshold")
    or _META_FALLBACK["news_block_confidence_threshold"])

_dsl = _meta.get("research_dsl") or {}
# research_daily.py only checks membership/ranges client-side; the actual
# pandas expressions stay server-side (that's the point of the split).
RESEARCH_FEATURES = {name: None for name in (_dsl.get("features") or [])}
RESEARCH_OPS = set(_dsl.get("ops") or [])
RESEARCH_PARAMS = {k: tuple(v) for k, v in (_dsl.get("params") or {}).items()}


# ---------------------------------------------------------------------------
# judgment functions (same interface as judgment_logic / judgment_backend_rule)
# ---------------------------------------------------------------------------

def classify_regime(ohlcv_by_pair):
    try:
        d = _post("/v1/judge/regime", {"ohlcv_by_pair": ohlcv_by_pair})
        return d["regime"], d.get("note", ""), d.get("model", MODEL_LABEL)
    except (X402ConfigError, X402PaymentRequired):
        raise
    except Exception as exc:
        print(f"[judgment_x402] classify_regime failed, rule fallback: {str(exc)[:120]}",
              flush=True)
        return _rule_fallback.classify_regime(ohlcv_by_pair)


def decide_directive(ohlcv_by_pair, history):
    try:
        d = _post("/v1/judge/directive",
                  {"ohlcv_by_pair": ohlcv_by_pair, "history": history})
        return d["directive"], d.get("note", ""), d.get("model", MODEL_LABEL)
    except (X402ConfigError, X402PaymentRequired):
        raise
    except Exception as exc:
        print(f"[judgment_x402] decide_directive failed, rule fallback: {str(exc)[:120]}",
              flush=True)
        return _rule_fallback.decide_directive(ohlcv_by_pair, history)


def write_status_change_article(prev_value, new_value, note, ohlcv_by_pair):
    try:
        return _post("/v1/write/status-article",
                     {"prev_value": prev_value, "new_value": new_value,
                      "note": note, "ohlcv_by_pair": ohlcv_by_pair})
    except (X402ConfigError, X402PaymentRequired):
        raise
    except Exception as exc:
        print(f"[judgment_x402] write_status_change_article failed, rule fallback: "
              f"{str(exc)[:120]}", flush=True)
        return _rule_fallback.write_status_change_article(
            prev_value, new_value, note, ohlcv_by_pair)


def review_trade(t, ctx):
    try:
        trade = {"exit_reason": (t or {}).get("exit_reason") if isinstance(t, dict)
                 else getattr(t, "exit_reason", None)}
        return _post("/v1/judge/postmortem", {"trade": trade, "ctx": ctx})
    except (X402ConfigError, X402PaymentRequired):
        raise
    except Exception as exc:
        print(f"[judgment_x402] review_trade failed, rule fallback: {str(exc)[:120]}",
              flush=True)
        return _rule_fallback.review_trade(t, ctx)


def propose_hypotheses(dossier):
    d = _post("/v1/research/hypotheses", {"dossier": dossier})
    return d.get("hypotheses") or []


def build_variant_strategy_code(h):
    d = _post("/v1/research/variant-code", {"hypothesis": h})
    return d["code"]


def write_blog_article(ctx, is_daily_summary, seo_topic=None):
    try:
        return _post("/v1/write/blog-article",
                     {"ctx": ctx, "is_daily_summary": bool(is_daily_summary),
                      "seo_topic": seo_topic})
    except (X402ConfigError, X402PaymentRequired):
        raise
    except Exception as exc:
        print(f"[judgment_x402] write_blog_article failed, rule fallback: "
              f"{str(exc)[:120]}", flush=True)
        return _rule_fallback.write_blog_article(ctx, is_daily_summary, seo_topic)


def write_satellite_article(channel, title, permalink, body_markdown):
    try:
        return _post("/v1/write/satellite-article",
                     {"channel": channel, "title": title, "permalink": permalink,
                      "body_markdown": body_markdown})
    except (X402ConfigError, X402PaymentRequired):
        raise
    except requests.HTTPError as exc:
        # 502 = 品質ゲート落ち。元実装の契約どおりNoneを返す(呼び出し側がスキップ)
        if exc.response is not None and exc.response.status_code == 502:
            return None
        print(f"[judgment_x402] write_satellite_article failed: {str(exc)[:120]}",
              flush=True)
        return None
    except Exception as exc:
        print(f"[judgment_x402] write_satellite_article failed: {str(exc)[:120]}",
              flush=True)
        return None


def classify_news(article, symbols):
    try:
        d = _post("/v1/judge/news", {"article": article, "symbols": symbols})
        return d.get("signals") or []
    except (X402ConfigError, X402PaymentRequired):
        raise
    except Exception as exc:
        print(f"[judgment_x402] classify_news failed, rule fallback: {str(exc)[:120]}",
              flush=True)
        return _rule_fallback.classify_news(article, symbols)
