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


def volume_ratio(candles, recent=4, baseline=30):
    """直近`recent`本の1本あたり出来高 ÷ 直近`baseline`本の1本あたり平均出来高。
    Crypto Fear&Greed Indexのvolume/momentum要素(25%)相当。出来高を伴う下落は
    伴わない下落より強いシグナル(投げ売り) -- Alternative.meの手法を参考に追加。"""
    if len(candles) < baseline:
        return None
    recent_avg = sum(c[5] for c in candles[-recent:]) / recent
    baseline_avg = sum(c[5] for c in candles[-baseline:]) / baseline
    if baseline_avg <= 0:
        return None
    return recent_avg / baseline_avg


def range_ratio(candles, recent=4, baseline=30):
    """直近`recent`本の(高値-安値)/終値 平均 ÷ 直近`baseline`本の同平均。
    Fear&Greed Indexのvolatility要素(25%)相当。方向を問わず値幅が急拡大している
    こと自体が地合いの不安定さ(fear)のシグナル。"""
    if len(candles) < baseline:
        return None
    def avg_range_pct(cs):
        vals = [(c[2] - c[3]) / c[4] for c in cs if c[4]]
        return sum(vals) / len(vals) if vals else None
    recent_r = avg_range_pct(candles[-recent:])
    baseline_r = avg_range_pct(candles[-baseline:])
    if not baseline_r:
        return None
    return recent_r / baseline_r


def build_stats_block(ohlcv_by_pair):
    lines = []
    chg_4h_values = []
    vol_ratios = []
    range_ratios = []
    for pair, candles in ohlcv_by_pair.items():
        if not candles:
            lines.append(f"{pair}: データなし")
            continue
        chg_4h = pct_change(candles, 4)
        chg_24h = pct_change(candles, 24)
        vr = volume_ratio(candles)
        rr = range_ratio(candles)
        if chg_4h is not None:
            chg_4h_values.append(chg_4h)
        if vr is not None:
            vol_ratios.append(vr)
        if rr is not None:
            range_ratios.append(rr)
        fmt = lambda v: ("%+.2f%%" % v) if v is not None else "N/A"
        fmt_ratio = lambda v: ("%.1fx" % v) if v is not None else "N/A"
        lines.append(
            f"{pair}: 4h {fmt(chg_4h)} / 24h {fmt(chg_24h)} "
            f"/ 出来高{fmt_ratio(vr)} / 値幅{fmt_ratio(rr)}"
        )

    summary_lines = []
    if chg_4h_values:
        n = len(chg_4h_values)
        mean_chg = sum(chg_4h_values) / n
        variance = sum((v - mean_chg) ** 2 for v in chg_4h_values) / n
        stdev = variance ** 0.5
        pos = sum(1 for v in chg_4h_values if v > 0)
        neg = sum(1 for v in chg_4h_values if v < 0)
        summary_lines.append(
            f"4h変化率: 平均{mean_chg:+.2f}%, 標準偏差{stdev:.2f}%"
            f"(大きいほど銘柄間でばらつきが大きく、判定の確信度は下げるべき)"
        )
        summary_lines.append(f"上昇/下落: {pos}銘柄 / {neg}銘柄 (計{n}銘柄)")
    if vol_ratios:
        summary_lines.append(
            f"出来高倍率: 平均{sum(vol_ratios)/len(vol_ratios):.2f}倍"
            f"(1.0=普段通り、大きいほど値動きに出来高が伴っている)"
        )
    if range_ratios:
        summary_lines.append(
            f"値幅倍率: 平均{sum(range_ratios)/len(range_ratios):.2f}倍"
            f"(1.0=普段通り、大きいほどボラティリティが拡大=不安定)"
        )

    header = "=== 市場全体サマリー ===\n" + "\n".join(summary_lines) if summary_lines else ""
    return (header + "\n\n=== 銘柄別 ===\n" if header else "") + "\n".join(lines)


def call_ollama(stats_block):
    prompt = f"""あなたは暗号資産市場のアナリストです。以下は主要銘柄の直近の価格変化率・出来高・値幅(ボラティリティ)です。

{stats_block}

これらを踏まえて、市場全体の地合いを判定してください。
- 上昇/下落の銘柄数が僅差、または標準偏差が大きい(=銘柄ごとにバラバラ)場合は、
  無理にbullish/bearishと決めつけずneutralを選んでよい
- 出来高倍率が高い(1.5倍以上など)方向への値動きは、出来高を伴わない値動きより
  信頼性が高いシグナルとして重視する
- 値幅倍率が急拡大している場合は、方向が定まっていなくても地合いが不安定な
  兆候として扱ってよい

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
