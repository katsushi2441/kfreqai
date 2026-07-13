"""Rule-based judgment backend -- no LLM, no external dependency, always
works. This is what a fresh clone runs by default (see judgment_backend.py).

Deliberately simple: this exists so the pipeline never crashes and always
produces *something*, not to be a good trading signal. Tasks that are
inherently meaningless without an LLM (creative hypothesis proposals,
market commentary) say so explicitly rather than silently doing nothing --
see NEEDS_LLM_NOTE.
"""
import datetime
import json

MODEL_LABEL = "rule-based-default"

NEEDS_LLM_NOTE = (
    "この機能はLLMバックエンドが必要です。KFREQAI_JUDGMENT_BACKEND=local_llm を設定し、"
    "kurage-advisory/judgment_logic.py を用意するか、x402バックエンド(利用可能になり次第)を"
    "設定してください。"
)


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def _pct_change(candles, back):
    if len(candles) <= back:
        return None
    latest_close = candles[-1][4]
    prev_close = candles[-1 - back][4]
    if not prev_close:
        return None
    return (latest_close - prev_close) / prev_close * 100.0


def classify_regime(ohlcv_by_pair):
    changes = []
    for candles in ohlcv_by_pair.values():
        chg = _pct_change(candles, 4)
        if chg is not None:
            changes.append(chg)
    if not changes:
        return "neutral", "データ不足のためneutral", MODEL_LABEL
    mean_chg = _mean(changes)
    if mean_chg > 0.5:
        return "bullish", f"4h変化率平均{mean_chg:+.2f}% (>+0.5%)", MODEL_LABEL
    if mean_chg < -0.5:
        return "bearish", f"4h変化率平均{mean_chg:+.2f}% (<-0.5%)", MODEL_LABEL
    return "neutral", f"4h変化率平均{mean_chg:+.2f}% (閾値内)", MODEL_LABEL


def decide_directive(ohlcv_by_pair, history):
    """regimeをそのままdirectiveに写す単純ルール(bearish→risk_off、bullish→risk_on)。"""
    regime, note, _ = classify_regime(ohlcv_by_pair)
    mapping = {"bearish": "risk_off", "bullish": "risk_on", "neutral": "neutral"}
    directive = mapping[regime]
    return directive, f"regime={regime}をそのまま採用 ({note})", MODEL_LABEL


def write_status_change_article(prev_value, new_value, note, ohlcv_by_pair):
    transition = "許可→停止" if new_value == "risk_off" else "停止→許可"
    title = f"リスク方針を{transition}に切り替え({MODEL_LABEL})"
    body = (
        f"リスク方針が {prev_value} から {new_value} に切り替わりました({transition})。\n\n"
        f"理由: {note}\n\n"
        "この記事はルールベースの自動判定で生成されています(LLMは使用していません)。\n"
    )
    slug = f"directive-change-{new_value}-{datetime.date.today():%Y%m%d}"
    return {"title": title, "slug": slug, "body": body}


def review_trade(t, ctx):
    """決済理由と結果だけから単純に分類する(反省会の代わり)。"""
    exit_reason = t.get("exit_reason") or ""
    result_pct = ctx.get("result_pct") or 0.0
    if exit_reason == "stop_loss":
        category = "stale_loser" if (ctx.get("held_hours") or 0) > 2 else "flash_crash"
    elif exit_reason == "roi":
        category = "normal_roi"
    elif result_pct < 0:
        category = "early_exit"
    else:
        category = "other"
    verdict = "good_entry" if result_pct > 0 else "bad_entry"
    return {
        "category": category, "verdict": verdict,
        "lesson": f"exit_reason={exit_reason}, result={result_pct:+.2f}% ({MODEL_LABEL})",
    }


# ルールベースはproposeを常に空で返すため使わないが、research_daily.pyが
# インポート時に参照するので空の状態で用意しておく。
RESEARCH_FEATURES = {}
RESEARCH_OPS = set()
RESEARCH_PARAMS = {}


def propose_hypotheses(dossier):
    print(f"[judgment_backend_rule] propose_hypotheses: {NEEDS_LLM_NOTE}", flush=True)
    return []


def build_variant_strategy_code(hypothesis):
    raise RuntimeError(NEEDS_LLM_NOTE)


def write_blog_article(ctx, is_daily_summary, seo_topic=None):
    """blog_post.pyのfallback_articleと同じ思想: 実測データだけを機械的に整形する。"""
    balance_total = ctx.get("balance", {}).get("total", "不明")
    profit_pct = ctx.get("profit", {}).get("profit_all_percent", "不明")
    winrate = ctx.get("profit", {}).get("winrate", "不明")
    open_positions = len(ctx.get("status", []))
    title = "dry-run取引botの1日総括" if is_daily_summary else "dry-run取引botの定時レポート"
    slug = "dry-run-daily-recap" if is_daily_summary else "dry-run-trading-update"
    body = (
        f"## 運用状況(ルールベース自動生成、{MODEL_LABEL})\n\n"
        f"残高(シミュレーション): {balance_total} USDT\n"
        f"累計損益: {profit_pct}%(勝率 {winrate})\n"
        f"保有中ポジション数: {open_positions}\n\n"
        "この記事はLLMを使わずルールベースで生成されています。\n"
    )
    return {"title": title, "slug": slug, "body": body}


def write_satellite_article(channel, title, permalink, body_markdown):
    print(f"[judgment_backend_rule] write_satellite_article: {NEEDS_LLM_NOTE}", flush=True)
    return None


NEWS_EVENT_TYPES = ["listing", "partnership", "upgrade", "hack", "exploit",
                     "delisting", "rug_pull", "lawsuit", "regulatory", "other"]
NEWS_NEGATIVE_EVENTS = {"hack", "exploit", "delisting", "rug_pull", "lawsuit"}
NEWS_BLOCK_CONFIDENCE_THRESHOLD = 0.6


def classify_news(article, symbols):
    """キーワード一致だけで判定する単純フィルタ。"""
    text = (article.get("title", "") + " " + article.get("summary", "")).lower()
    negative_keywords = {
        "hack": "hack", "exploit": "exploit", "delist": "delisting",
        "rug pull": "rug_pull", "rugpull": "rug_pull", "lawsuit": "lawsuit",
        "sued": "lawsuit",
    }
    hits = [(kw, ev) for kw, ev in negative_keywords.items() if kw in text]
    results = []
    for sym in symbols:
        if hits:
            _, event_type = hits[0]
            results.append({"symbol": sym, "sentiment": "negative",
                             "event_type": event_type, "confidence": 0.7})
        else:
            results.append({"symbol": sym, "sentiment": "neutral",
                             "event_type": "other", "confidence": 0.3})
    return results
