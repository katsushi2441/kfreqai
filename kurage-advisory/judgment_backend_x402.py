"""x402 paid-API judgment backend -- STUB.

Intended future shape: call a hosted kfreqai judgment API (advisory_api.py
or a successor, paid via x402) instead of running any LLM locally --
matching the same pattern already used by llm-gateway (port 8019, Bankr
x402, Gemma4) in this workspace. Lets a user run kfreqai's advisory loops
with no LLM, no API keys, and no GPU of their own: pay per call instead.

Status: the REST side now exists -- judgment_api.py serves all judgment
functions on 127.0.0.1:18321 (see JUDGMENT_API.md). What's still missing is
the x402 payment gateway in front of it and the HTTP client here. When
wiring this up, the gateway MUST force engine="gemma" on every call so the
personal-subscription Claude CLI never serves paid traffic.

Every function here still raises clearly rather than pretending to work.
"""
NOT_AVAILABLE_NOTE = (
    "x402バックエンドはまだ利用できません(有料判定APIは未提供)。"
    "KFREQAI_JUDGMENT_BACKEND=rule_based または local_llm を使ってください。"
)


class X402BackendNotAvailable(RuntimeError):
    pass


RESEARCH_FEATURES = {}
RESEARCH_OPS = set()
RESEARCH_PARAMS = {}


def classify_regime(ohlcv_by_pair):
    raise X402BackendNotAvailable(NOT_AVAILABLE_NOTE)


def decide_directive(ohlcv_by_pair, history):
    raise X402BackendNotAvailable(NOT_AVAILABLE_NOTE)


def write_status_change_article(prev_value, new_value, note, ohlcv_by_pair):
    raise X402BackendNotAvailable(NOT_AVAILABLE_NOTE)


def review_trade(t, ctx):
    raise X402BackendNotAvailable(NOT_AVAILABLE_NOTE)


def propose_hypotheses(dossier):
    raise X402BackendNotAvailable(NOT_AVAILABLE_NOTE)


def build_variant_strategy_code(hypothesis):
    raise X402BackendNotAvailable(NOT_AVAILABLE_NOTE)


def write_blog_article(ctx, is_daily_summary, seo_topic=None):
    raise X402BackendNotAvailable(NOT_AVAILABLE_NOTE)


def write_satellite_article(channel, title, permalink, body_markdown):
    raise X402BackendNotAvailable(NOT_AVAILABLE_NOTE)


NEWS_EVENT_TYPES = ["listing", "partnership", "upgrade", "hack", "exploit",
                     "delisting", "rug_pull", "lawsuit", "regulatory", "other"]
NEWS_NEGATIVE_EVENTS = {"hack", "exploit", "delisting", "rug_pull", "lawsuit"}
NEWS_BLOCK_CONFIDENCE_THRESHOLD = 0.6


def classify_news(article, symbols):
    raise X402BackendNotAvailable(NOT_AVAILABLE_NOTE)
