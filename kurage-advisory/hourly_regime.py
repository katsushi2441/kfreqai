#!/usr/bin/env python3
"""Hourly market-regime tagger for kfreqai.

Fetches recent OHLCV for the bot's whitelisted pairs (via ccxt inside the
kfreqai container) and asks the active judgment backend
(see judgment_backend.py) to classify the overall regime as
bullish / bearish / neutral. Writes the result to
user_data/advisory_state.json for the freqtrade strategy to read.

早期ディレクティブ再評価(2026-07-13追加、2026-07-13対称化): directive
(8時間おき)は毎時のregime変化を即座には拾わないため、最大8時間古いまま
放置される。regimeが実際に転じた瞬間(=遷移。毎時同じ判定が続くだけでは
発火しない)だけ、daily_directive.main()を即時に呼んで再評価する。無駄な
LLM呼び出しを避けるため、状態が矛盾している(=directiveを変える意味がある)
ときだけ発火する、両方向対称の仕組み:
  - 解除方向: bearish→bullish/neutral に転じた かつ directiveがrisk_off
  - ブロック方向: bullish/neutral→bearish に転じた かつ directiveがrisk_off以外

判定ロジック本体(統計の計算式・プロンプト文面)は judgment_backend 経由で
差し替え可能(local_llm=judgment_logic.py[非公開] / rule_based / x402)。
ここはデータ取得・state書き込み・再評価トリガーといったパイプライン部分だけ。
"""
import json
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "user_data", "strategies"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import advisory_state
import judgment_backend

CONFIG_PATH = os.path.join(BASE_DIR, "user_data", "config.json")
CONTAINER_NAME = "kfreqai"


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


def compute_relative_strength(ohlcv_by_pair):
    """4h変化率のペア間相対強度。regimeがbearishでも、この銘柄だけ市場平均より
    明確に強ければconfirm_trade_entryで個別に許可するためのデータ。汎用的な
    計算式(判定ロジックの秘匿対象ではない)なのでここに置く。"""
    def pct_change(candles, back):
        if len(candles) <= back:
            return None
        latest_close = candles[-1][4]
        prev_close = candles[-1 - back][4]
        if not prev_close:
            return None
        return (latest_close - prev_close) / prev_close * 100.0

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

    prev_regime = (advisory_state.read_state().get("regime") or {}).get("value")

    ohlcv_by_pair = fetch_ohlcv(exchange_name, pairs)

    try:
        regime, note, model_label = judgment_backend.classify_regime(ohlcv_by_pair)
    except Exception as exc:
        print(f"[hourly_regime] classify_regime failed, defaulting to neutral: {exc}", flush=True)
        regime, note, model_label = "neutral", "判定失敗のためneutral", "none-failopen"

    entry = advisory_state.write_regime(regime, note, model_label)
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
