#!/usr/bin/env python3
"""Hourly market-regime tagger for kfreqai, using local gemma4 via Ollama.

Fetches recent OHLCV for the bot's whitelisted pairs (via ccxt inside the
kfreqai container), computes simple % change stats, and asks gemma4 to
classify the overall regime as bullish / bearish / neutral. Writes the
result to user_data/advisory_state.json for the freqtrade strategy to read.

gemma4 is a thinking model: "think" must be False, otherwise hidden
reasoning tokens eat num_predict and the response comes back empty
(see /home/kojima/work/CLAUDE.md).
"""
import json
import os
import re
import subprocess
import sys

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "user_data", "strategies"))
import advisory_state

CONFIG_PATH = os.path.join(BASE_DIR, "user_data", "config.json")
CONTAINER_NAME = "kfreqai"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://192.168.0.14:11434")
OLLAMA_MODEL = os.environ.get("KFREQAI_OLLAMA_MODEL", "gemma4:12b-it-qat")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_ohlcv(exchange_name, pairs, timeframe="1h", limit=30):
    """Fetch OHLCV for each pair via ccxt inside the running kfreqai container."""
    script = f"""
import ccxt, json
ex = getattr(ccxt, {exchange_name!r})()
out = {{}}
for pair in {pairs!r}:
    try:
        out[pair] = ex.fetch_ohlcv(pair, {timeframe!r}, limit={limit})
    except Exception as e:
        out[pair] = []
print(json.dumps(out))
"""
    result = subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "python3", "-c", script],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError("ccxt fetch failed: %s" % (result.stderr or result.stdout)[-500:])
    return json.loads(result.stdout)


def pct_change(candles, back):
    """% change from `back` candles ago to the latest close."""
    if len(candles) <= back:
        return None
    latest_close = candles[-1][4]
    prev_close = candles[-1 - back][4]
    if not prev_close:
        return None
    return (latest_close - prev_close) / prev_close * 100.0


def build_stats_block(ohlcv_by_pair):
    lines = []
    for pair, candles in ohlcv_by_pair.items():
        if not candles:
            lines.append(f"{pair}: データなし")
            continue
        chg_4h = pct_change(candles, 4)
        chg_24h = pct_change(candles, 24)
        fmt = lambda v: ("%+.2f%%" % v) if v is not None else "N/A"
        lines.append(f"{pair}: 4h {fmt(chg_4h)} / 24h {fmt(chg_24h)}")
    return "\n".join(lines)


def call_ollama(stats_block):
    prompt = f"""あなたは暗号資産市場のアナリストです。以下は主要銘柄の直近の価格変化率です。

{stats_block}

これらを踏まえて、市場全体の地合いを判定してください。
出力は必ず以下の2行だけの形式で、他の文章は書かないでください。
REGIME: bullish または bearish または neutral のいずれか一語
NOTE: 30文字以内の日本語の一言理由
"""
    payload = {
        "model": OLLAMA_MODEL,
        "think": False,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 200},
    }
    resp = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json().get("response") or ""


def parse_response(text):
    regime_match = re.search(r"REGIME:\s*(bullish|bearish|neutral)", text, re.IGNORECASE)
    note_match = re.search(r"NOTE:\s*(.+)", text)
    regime = regime_match.group(1).lower() if regime_match else "neutral"
    note = note_match.group(1).strip()[:120] if note_match else ""
    return regime, note


def compute_relative_strength(ohlcv_by_pair):
    """4h変化率のペア間相対強度。regimeがbearishでも、この銘柄だけ市場平均より
    明確に強ければconfirm_trade_entryで個別に許可するためのデータ。"""
    chg_4h_by_pair = {}
    for pair, candles in ohlcv_by_pair.items():
        chg = pct_change(candles, 4)
        if chg is not None:
            chg_4h_by_pair[pair] = chg
    if not chg_4h_by_pair:
        return {}, None
    market_avg = sum(chg_4h_by_pair.values()) / len(chg_4h_by_pair)
    pairs_data = {
        pair: {"chg_4h": chg, "rel_4h": chg - market_avg}
        for pair, chg in chg_4h_by_pair.items()
    }
    return pairs_data, market_avg


def main():
    config = load_config()
    exchange_name = config["exchange"]["name"]
    pairs = config["exchange"]["pair_whitelist"]

    ohlcv_by_pair = fetch_ohlcv(exchange_name, pairs)
    stats_block = build_stats_block(ohlcv_by_pair)

    try:
        response_text = call_ollama(stats_block)
        regime, note = parse_response(response_text)
    except Exception as exc:
        print(f"[hourly_regime] ollama call failed, defaulting to neutral: {exc}", flush=True)
        regime, note = "neutral", "ollama呼び出し失敗のためneutral"

    entry = advisory_state.write_regime(regime, note, OLLAMA_MODEL)
    print(f"[hourly_regime] {entry['updated_at_iso']} regime={regime} note={note!r}", flush=True)

    pairs_data, market_avg = compute_relative_strength(ohlcv_by_pair)
    if pairs_data:
        advisory_state.write_strength(pairs_data, market_avg, "ccxt:4h_vs_basket")
        top = sorted(pairs_data.items(), key=lambda kv: -kv[1]["rel_4h"])[:3]
        top_note = ", ".join(f"{p}:{d['rel_4h']:+.1f}pt" for p, d in top)
        print(f"[hourly_regime] strength market_avg_4h={market_avg:+.2f}% top={top_note}", flush=True)


if __name__ == "__main__":
    main()
