#!/usr/bin/env python3
"""3x/day risk directive for kfreqai.

Runs at 05:00 / 13:00 / 21:00 JST (plus early re-checks triggered by
hourly_regime.py). Asks the active judgment backend (see
judgment_backend.py) for a coarse risk directive (risk_on / risk_off /
neutral) for the freqtrade strategy to read. This only ever runs
live/forward -- it cannot be backtested, since an LLM's own knowledge
already contains history past any given backtest date.

Also posts a Bludit-only blog record (no announcement, no hatena/Blogger
crosspost) whenever the risk_off <-> non-risk_off boundary crosses,
regardless of whether the run was scheduled or an early re-check.

resolve_claude_bin / resolve_codex_bin are re-exported here (thin wrappers
around llm_client) because blog_post.py and research_daily.py import them
from this module.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "user_data", "strategies"))
import advisory_state
import judgment_backend
import llm_client
from hourly_regime import load_config, fetch_ohlcv
# blog_post is imported lazily inside post_status_change_article() -- blog_post.py
# itself does `from daily_directive import resolve_claude_bin` at module level,
# so importing it here at module level would be circular.

resolve_claude_bin = llm_client.resolve_claude_bin
resolve_codex_bin = llm_client.resolve_codex_bin


def post_status_change_article(prev_value, new_value, note, ohlcv_by_pair):
    """許可/停止が切り替わった記録をBluditにだけ投稿する(告知・はてな/Blogger転載はしない)。"""
    import blog_post

    try:
        article = judgment_backend.write_status_change_article(prev_value, new_value, note, ohlcv_by_pair)
    except Exception as exc:
        print(f"[daily_directive] status-change article generation failed: {exc}", flush=True)
        return
    if not article:
        print("[daily_directive] status-change article: backend returned nothing, skipping post", flush=True)
        return

    body_with_footer = article["body"] + blog_post.DISCLOSURE_FOOTER
    try:
        _, permalink = blog_post.post_to_bludit(article["title"], article["slug"], body_with_footer)
        print(f"[daily_directive] status-change article posted: {article['title']} -> {permalink}", flush=True)
    except Exception as exc:
        print(f"[daily_directive] status-change article: post failed: {exc}", flush=True)


def main():
    config = load_config()
    exchange_name = config["exchange"]["name"]
    pairs = config["exchange"]["pair_whitelist"]

    prev_directive_value = (advisory_state.read_state().get("directive") or {}).get("value")

    ohlcv_by_pair = fetch_ohlcv(exchange_name, pairs)
    history = advisory_state.read_recent_history(max_entries=12)

    try:
        directive, note, model_used = judgment_backend.decide_directive(ohlcv_by_pair, history)
    except Exception as exc:
        print(f"[daily_directive] decide_directive failed, defaulting to neutral: {exc}", flush=True)
        directive, note, model_used = "neutral", "判定失敗のためneutral", "none-failopen"

    entry = advisory_state.write_directive(directive, note, model_used)
    print(f"[daily_directive] {entry['updated_at_iso']} directive={directive} note={note!r}", flush=True)

    if prev_directive_value is not None:
        was_blocked = prev_directive_value == "risk_off"
        now_blocked = directive == "risk_off"
        if was_blocked != now_blocked:
            print(f"[daily_directive] entry status changed ({prev_directive_value} -> {directive}), "
                  "posting blog record", flush=True)
            post_status_change_article(prev_directive_value, directive, note, ohlcv_by_pair)


if __name__ == "__main__":
    main()
