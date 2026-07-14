"""FastAPI service exposing user_data/advisory_state.json (regime/directive
judgement) for the dashboard.

kfreqai.php (kurage_web) runs on a separate host (heteml) and can't read
this host's files directly. freqtrade's own REST API (18313) doesn't know
about this custom advisory concept and offers no supported plugin point for
adding routes to it without forking upstream (see project discussion,
2026-07-13), so this is a small standalone API instead.

No new externally-facing port: binds 127.0.0.1 only. It's reached through
the already-public 18314 nginx server via a path (see
/etc/nginx/conf.d/kfreqai-18314.conf, location /advisory-state), which
already forwards FreqUI traffic to 18313 -- this just adds one more path
proxied to this process instead of freqtrade.
"""
import os
import datetime
import json
import sys

from fastapi import FastAPI

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_BASE, "user_data", "strategies"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import advisory_state  # noqa: E402
import market_facts  # noqa: E402

NEWS_SIGNALS_PATH = os.path.join(_BASE, "user_data", "news_signals.json")

app = FastAPI()


@app.get("/advisory-state")
def get_advisory_state():
    """regime/directiveに加えmarket_facts(銘柄ニュース+ブロック)を同梱して返す。
    nginx(18314)は location = /advisory-state の完全一致プロキシなので、
    サブパスを増やさず1つの応答に相乗りさせる(sudo無しで済む設計判断)。"""
    state = advisory_state.read_state()
    state["market_facts"] = get_market_facts()
    return state


@app.get("/advisory-state/market-facts")
def get_market_facts():
    """有効期限内の銘柄ニュース事実 + アクティブな24hブロック(ローカル直叩き用)。"""
    blocks = {}
    try:
        data = json.load(open(NEWS_SIGNALS_PATH))
        cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(hours=24)).isoformat()[:19]
        for sym, sig in (data.get("blocks") or {}).items():
            if sig.get("at", "") > cutoff:
                blocks[sym] = sig
    except Exception:
        pass

    facts = []
    try:
        conn = market_facts.connect()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()[:19]
        rows = conn.execute(
            "SELECT pair, raw_title, sentiment, event_type, confidence,"
            " observed_at, source_url FROM facts"
            " WHERE expires_at IS NULL OR expires_at > ?"
            " ORDER BY observed_at DESC LIMIT 30", (now,)).fetchall()
        facts = [dict(r) for r in rows]
    except Exception:
        pass
    return {"blocks": blocks, "facts": facts}
