#!/usr/bin/env python3
"""facts_collect — 保有中/直近損失銘柄のニュースを収集してmarket_factsに蓄積する。

ユーザー方針(2026-07-14): 全129銘柄の漫然収集はしない。対象は
「今保有中の銘柄」+「直近24hの最大損失銘柄」だけに絞る(1日数銘柄)。
ソースはGoogle News RSS(銘柄シンボル検索、認証不要でcron安全)。
分類は既存のjudgment_backend.classify_news(news_signal.pyと同じ経路)。

シャドー運用: 蓄積した事実はpostmortemの判断材料に添付されるだけで、
取引ゲートには直結しない。昇格は実効果検証後。
"""
import datetime
import re
import sys
import os
import urllib.parse
import xml.etree.ElementTree as ET

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import judgment_backend
import market_facts
from blog_post import freqtrade_base_and_token, freqtrade_get
from news_signal import AMBIGUOUS, SIGNALS_PATH  # 一般語シンボル除外・ブロックファイル共有

MAX_ARTICLES_PER_PAIR = 5
NEGATIVE_TTL_H = 24    # news_signal.pyの24hブロックと同じ時定数
DEFAULT_TTL_H = 72


def target_bases():
    """保有中の銘柄 + 直近24hの最大損失銘柄。"""
    base, token = freqtrade_base_and_token()
    bases = []
    for t in freqtrade_get(base, token, "/api/v1/status"):
        b = t["pair"].split("/")[0]
        if b not in bases:
            bases.append(b)
    trades = freqtrade_get(base, token, "/api/v1/trades?limit=100").get("trades", [])
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(hours=24)).timestamp() * 1000
    recent = [t for t in trades if not t["is_open"]
              and (t.get("close_timestamp") or 0) >= cutoff]
    if recent:
        worst = min(recent, key=lambda t: t.get("close_profit_abs") or 0)
        b = worst["pair"].split("/")[0]
        if b not in bases:
            bases.append(b)
    return bases


def fetch_news_rss(symbol):
    q = urllib.parse.quote(f'"{symbol}" crypto')
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    r = requests.get(url, timeout=30, headers={"User-Agent": "kfreqai-facts/1.0"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")[:400].strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if title:
            items.append({"title": title, "summary": desc, "link": link, "pub": pub})
    return items[:MAX_ARTICLES_PER_PAIR]


def write_entry_blocks(neg_facts):
    """negative高確度イベントをnews_signals.jsonのblocksへ追記する。

    「negative(hack/exploit/delisting等)高confidenceのみ24hエントリー禁止」は
    news_signal.pyで運用承認済みの既存ポリシー。ここはその適用範囲を
    保有中/損失銘柄の狙い撃ち収集にも広げるだけで、方向は取引を止める側のみ。
    """
    if not neg_facts:
        return
    import json as _json
    data = {"updated_at": "", "blocks": {}}
    if os.path.exists(SIGNALS_PATH):
        try:
            data = _json.load(open(SIGNALS_PATH))
        except Exception:
            pass
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()[:19]
    data["updated_at"] = now
    blocks = data.setdefault("blocks", {})
    for f in neg_facts:
        blocks[f["symbol"]] = {
            "at": now, "symbol": f["symbol"], "sentiment": "negative",
            "event_type": f["event_type"], "confidence": f["confidence"],
            "title": f["title"][:120], "link": f["link"], "source": "facts_collect",
        }
        print(f"[facts] BLOCK {f['symbol']}: {f['event_type']} ({f['title'][:60]})")
    _json.dump(data, open(SIGNALS_PATH, "w"), ensure_ascii=False, indent=1)


def main():
    conn = market_facts.connect()
    bases = [b for b in target_bases() if b not in AMBIGUOUS]
    print(f"[facts] 対象銘柄: {bases}")
    n_new = 0
    neg_facts = []
    for sym in bases:
        try:
            items = fetch_news_rss(sym)
        except Exception as e:
            print(f"[facts] {sym} RSS失敗: {str(e)[:80]}")
            continue
        for art in items:
            try:
                sigs = judgment_backend.classify_news(art, [sym])
            except Exception as e:
                print(f"[facts] {sym} 分類失敗: {str(e)[:80]}")
                continue
            for sig in sigs:
                if str(sig.get("symbol", "")).upper() != sym:
                    continue
                ev = sig.get("event_type")
                neg = (sig.get("sentiment") == "negative"
                       and ev in judgment_backend.NEWS_NEGATIVE_EVENTS
                       and float(sig.get("confidence") or 0)
                       >= judgment_backend.NEWS_BLOCK_CONFIDENCE_THRESHOLD)
                added = market_facts.add_fact(
                    conn, sym,
                    fact=f"{art['title'][:140]} [{sig.get('sentiment')}/{ev}]",
                    event_type=ev, sentiment=sig.get("sentiment"),
                    confidence=sig.get("confidence"), source_url=art["link"],
                    ttl_hours=NEGATIVE_TTL_H if neg else DEFAULT_TTL_H,
                    raw_title=art["title"])
                if added:
                    n_new += 1
                    print(f"[facts] + {sym}: {sig.get('sentiment')}/{ev} {art['title'][:60]}")
                if neg:
                    neg_facts.append({"symbol": sym, "event_type": ev,
                                      "confidence": sig.get("confidence"),
                                      "title": art["title"], "link": art["link"]})
    write_entry_blocks(neg_facts)
    print(f"[facts] 完了: 新規{n_new}件, DB: {market_facts.stats(conn)}")


if __name__ == "__main__":
    main()
