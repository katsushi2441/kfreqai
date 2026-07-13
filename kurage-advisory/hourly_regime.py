#!/usr/bin/env python3
"""Hourly market-regime tagger for kfreqai, using local gemma4 via Ollama.

Fetches recent OHLCV for the bot's whitelisted pairs (via ccxt inside the
kfreqai container), computes simple % change stats, and asks gemma4 to
classify the overall regime as bullish / bearish / neutral. Writes the
result to user_data/advisory_state.json for the freqtrade strategy to read.

gemma4 is a thinking model: "think" must be False, otherwise hidden
reasoning tokens eat num_predict and the response comes back empty
(see /home/kojima/work/CLAUDE.md).

早期ディレクティブ再評価(2026-07-13追加、2026-07-13対称化): directive
(Claude、8時間おき)は毎時のregime変化を即座には拾わないため、最大8時間
古いまま放置される。regimeが実際に転じた瞬間(=遷移。毎時同じ判定が続くだけ
では発火しない)だけ、daily_directive.main()を即時に呼んで再評価する。
無駄なClaude/Codex呼び出しを避けるため、状態が矛盾している(=directiveを
変える意味がある)ときだけ発火する、両方向対称の仕組み:
  - 解除方向: bearish→bullish/neutral に転じた かつ directiveがrisk_off
  - ブロック方向: bullish/neutral→bearish に転じた かつ directiveがrisk_off以外

判定ロジック本体(統計の計算式・プロンプト文面)は judgment_logic.py
(gitignore対象、非公開)に分離している。ここはデータ取得・LLM呼び出し・
state書き込み・再評価トリガーといったパイプライン部分だけを持つ。
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
import judgment_logic

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


def build_stats_block(ohlcv_by_pair):
    return judgment_logic.build_stats_block(ohlcv_by_pair)


def call_ollama(stats_block):
    prompt = judgment_logic.build_regime_prompt(stats_block)
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
    return judgment_logic.compute_relative_strength(ohlcv_by_pair)


def main():
    config = load_config()
    exchange_name = config["exchange"]["name"]
    pairs = config["exchange"]["pair_whitelist"]

    prev_regime = (advisory_state.read_state().get("regime") or {}).get("value")

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

    early_recheck = False
    if (prev_regime == "bearish" and regime in ("bullish", "neutral")
            and advisory_state.effective_directive() == "risk_off"):
        print(f"[hourly_regime] regime flipped bearish->{regime} while directive=risk_off, "
              "triggering early directive re-check (unblock direction)", flush=True)
        early_recheck = True
    elif (prev_regime in ("bullish", "neutral") and regime == "bearish"
            and advisory_state.effective_directive() != "risk_off"):
        print(f"[hourly_regime] regime flipped {prev_regime}->bearish while directive="
              f"{advisory_state.effective_directive()}, triggering early directive re-check "
              "(block direction)", flush=True)
        early_recheck = True

    if early_recheck:
        try:
            import daily_directive
            daily_directive.main()
        except Exception as exc:
            print(f"[hourly_regime] early directive re-check failed: {exc}", flush=True)

    pairs_data, market_avg = compute_relative_strength(ohlcv_by_pair)
    if pairs_data:
        advisory_state.write_strength(pairs_data, market_avg, "ccxt:4h_vs_basket")
        top = sorted(pairs_data.items(), key=lambda kv: -kv[1]["rel_4h"])[:3]
        top_note = ", ".join(f"{p}:{d['rel_4h']:+.1f}pt" for p, d in top)
        print(f"[hourly_regime] strength market_avg_4h={market_avg:+.2f}% top={top_note}", flush=True)


if __name__ == "__main__":
    main()
