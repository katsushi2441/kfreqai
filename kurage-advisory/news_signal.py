#!/usr/bin/env python3
"""②セマンティック・ニュースシグナル — LightGBMが読めないテキスト情報をLLMが読む層。

毎時実行:
1. 暗号資産ニュースRSS(CoinDesk/Cointelegraph)を巡回
2. whitelist銘柄のシンボル/名称に言及する記事を抽出
3. gemma4が銘柄別に {sentiment, event_type, confidence} を分類
4. user_data/news_signals.json に書き出し

運用は「安全側から」始める:
- negative(hack/exploit/delisting)高confidenceのみ、戦略側が24hエントリー禁止に使う
- positiveシグナルは当面シャドー記録のみ(実効果を後日検証してから昇格させる。
  検証なしの買い材料をライブに直結させない — 2026-07-10の教訓)
"""
import datetime
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_common import BASE_DIR, LAB_DIR, ensure_lab_dir, jsonl_append
import judgment_backend

CONFIG_PATH = os.path.join(BASE_DIR, "user_data", "config.json")
SIGNALS_PATH = os.path.join(BASE_DIR, "user_data", "news_signals.json")
SHADOW_LOG = os.path.join(LAB_DIR, "news_shadow.jsonl")
SEEN_PATH = os.path.join(LAB_DIR, "news_seen.json")

FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
]

# 分類の閾値・カテゴリ・プロンプトは judgment_backend 経由(バックエンドごとに定義)。
NEGATIVE_EVENTS = judgment_backend.NEWS_NEGATIVE_EVENTS
EVENT_TYPES = judgment_backend.NEWS_EVENT_TYPES

# 英語の一般語と衝突するシンボルは記事マッチ対象から除外する(誤検知が利益より大きい)
AMBIGUOUS = {"H", "G", "W", "IN", "US", "CC", "CS", "DN", "IO", "PI", "MX", "UB",
             "SUN", "REAL", "STAR", "PLAY", "POWER", "SMART", "CORN", "TOFU",
             "BASED", "EPIC", "GOLD", "ASSET", "EDGE", "BEAT", "LIT", "CAP",
             "TAG", "NES", "ACN", "THE", "APE", "CAKE", "VINE", "GRASS", "SHOT",
             "ARROW", "PERM", "TRIA", "SEDA", "SYN", "PUMP", "H", "NEX", "NXT"}


def whitelist_bases():
    c = json.load(open(CONFIG_PATH))
    return [p.split("/")[0] for p in c["exchange"]["pair_whitelist"]]


def fetch_feed(url):
    r = requests.get(url, timeout=30, headers={"User-Agent": "kfreqai-news/1.0"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")[:400]
        link = (item.findtext("link") or "").strip()
        if title:
            items.append({"title": title, "summary": desc.strip(), "link": link})
    return items


def match_pairs(text, bases):
    """記事テキストに言及されているwhitelist銘柄を拾う(単語境界一致)。"""
    hits = []
    upper = text.upper()
    for base in bases:
        if base in AMBIGUOUS:
            continue  # 一般語シンボルはノイズが多すぎるので対象外
        if re.search(rf"\b{re.escape(base)}\b", upper):
            hits.append(base)
    return hits


def classify(article, symbols):
    try:
        return judgment_backend.classify_news(article, symbols)
    except Exception as exc:
        print(f"[news] classify_news failed: {str(exc)[:120]}")
        return []


def main():
    ensure_lab_dir()
    bases = whitelist_bases()
    seen = set()
    if os.path.exists(SEEN_PATH):
        seen = set(json.load(open(SEEN_PATH))[-2000:])

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()[:19]
    active_blocks = {}
    # 既存のシグナル(24時間以内のネガティブブロック)を引き継ぐ
    if os.path.exists(SIGNALS_PATH):
        prev = json.load(open(SIGNALS_PATH))
        cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(hours=24)).isoformat()[:19]
        for sym, sig in prev.get("blocks", {}).items():
            if sig.get("at", "") > cutoff:
                active_blocks[sym] = sig

    n_new = 0
    for feed in FEEDS:
        try:
            items = fetch_feed(feed)
        except Exception as e:
            print(f"[news] feed失敗 {feed[:40]}: {str(e)[:80]}")
            continue
        for art in items:
            key = art["link"] or art["title"]
            if key in seen:
                continue
            seen.add(key)
            symbols = match_pairs(art["title"] + " " + art["summary"], bases)
            if not symbols:
                continue
            n_new += 1
            for sig in classify(art, symbols):
                sym = str(sig.get("symbol", "")).upper()
                if sym not in bases:
                    continue
                record = {"at": now, "symbol": sym,
                          "sentiment": sig.get("sentiment"),
                          "event_type": sig.get("event_type"),
                          "confidence": sig.get("confidence"),
                          "title": art["title"][:120], "link": art["link"]}
                jsonl_append(SHADOW_LOG, record)  # 全シグナルをシャドー記録(後日検証用)
                if (sig.get("sentiment") == "negative"
                        and sig.get("event_type") in NEGATIVE_EVENTS
                        and float(sig.get("confidence") or 0) >= judgment_backend.NEWS_BLOCK_CONFIDENCE_THRESHOLD):
                    active_blocks[sym] = record
                    print(f"[news] BLOCK {sym}: {sig.get('event_type')} ({art['title'][:60]})")
                else:
                    print(f"[news] shadow {sym}: {sig.get('sentiment')}/{sig.get('event_type')}")

    json.dump({"updated_at": now, "blocks": active_blocks},
              open(SIGNALS_PATH, "w"), ensure_ascii=False, indent=1)
    json.dump(sorted(seen)[-2000:], open(SEEN_PATH, "w"))
    print(f"[news] 巡回完了: 新規言及記事={n_new}, 有効ブロック={list(active_blocks)}")


if __name__ == "__main__":
    main()
