"""Selects which judgment implementation the advisory pipeline uses.

KFREQAI_JUDGMENT_BACKEND env var:
  - "local_llm"  -- kurage-advisory/judgment_logic.py (private, real prompts
                    + your own Claude/Codex/gemma4). ImportError if that
                    file doesn't exist (you asked for it explicitly).
  - "rule_based" -- judgment_backend_rule.py, no LLM, always available.
  - "x402"       -- judgment_backend_x402.py, calls a paid hosted judgment
                    API instead of running any LLM locally. Not implemented
                    yet (see that module).
  - "auto" (default) -- try local_llm, silently fall back to rule_based if
                    judgment_logic.py isn't present. This is what makes a
                    fresh `git clone` + `docker compose up -d` work without
                    any LLM setup, while a production checkout with the
                    private module in place gets the real thing for free.

All pipeline files (hourly_regime.py, daily_directive.py, blog_post.py,
trade_postmortem.py, research_daily.py, news_signal.py) import this module
instead of importing judgment_logic / judgment_backend_rule directly.
"""
import os

_BACKEND = os.environ.get("KFREQAI_JUDGMENT_BACKEND", "auto").strip().lower()

if _BACKEND == "rule_based":
    import judgment_backend_rule as _impl
elif _BACKEND == "x402":
    import judgment_backend_x402 as _impl
elif _BACKEND == "local_llm":
    import judgment_logic as _impl
elif _BACKEND == "auto":
    try:
        import judgment_logic as _impl
    except ImportError:
        import judgment_backend_rule as _impl
        _BACKEND = "auto(rule_based fallback)"
    else:
        _BACKEND = "auto(local_llm)"
else:
    raise ValueError(f"unknown KFREQAI_JUDGMENT_BACKEND={_BACKEND!r} "
                      "(expected rule_based / local_llm / x402 / auto)")

BACKEND_NAME = _BACKEND
print(f"[judgment_backend] active backend: {BACKEND_NAME} (module={_impl.__name__})", flush=True)

classify_regime = _impl.classify_regime
decide_directive = _impl.decide_directive
write_status_change_article = _impl.write_status_change_article
review_trade = _impl.review_trade
propose_hypotheses = _impl.propose_hypotheses
build_variant_strategy_code = _impl.build_variant_strategy_code
write_blog_article = _impl.write_blog_article
write_satellite_article = _impl.write_satellite_article
classify_news = _impl.classify_news
# research_daily.pyのバリデーションが参照する仮説DSLの定義(rule_based/x402では空)
RESEARCH_FEATURES = _impl.RESEARCH_FEATURES
RESEARCH_OPS = _impl.RESEARCH_OPS
RESEARCH_PARAMS = _impl.RESEARCH_PARAMS
# news_signal.pyが参照する分類カテゴリ・閾値
NEWS_EVENT_TYPES = _impl.NEWS_EVENT_TYPES
NEWS_NEGATIVE_EVENTS = _impl.NEWS_NEGATIVE_EVENTS
NEWS_BLOCK_CONFIDENCE_THRESHOLD = _impl.NEWS_BLOCK_CONFIDENCE_THRESHOLD
