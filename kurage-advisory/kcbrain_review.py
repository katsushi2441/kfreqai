#!/usr/bin/env python3
"""kcbrainトレードレビュー・セカンドオピニオン — ①のシャドージョブ(2026-07-17開始)。

毎時実行。trade_postmortem.py が既に計算済みの文脈(trade_journal.jsonl:
MAE/MFE・エントリー前4h騰落・ニュース・gemma4自家製プロンプトの評決)を読み、
同一の証拠を kcbrain /v1/review/trade に渡してレビューさせ、両者を並べて
user_data/lab/kcbrain_review.jsonl に蓄積する。

- 数値は全てコード側で計算済みのものを使う(判定側に捏造余地を与えない、
  postmortemと同じ原則)。
- ライブの取引判断・advisory_state.json には一切影響しない。
- 目的: 自家製プロンプト vs kcbrain(構造化レビュー契約)の教訓品質A/B。
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_common import LAB_DIR, jsonl_append, jsonl_load
import kcbrain_client

JOURNAL_PATH = os.path.join(LAB_DIR, "trade_journal.jsonl")
REVIEW_PATH = os.path.join(LAB_DIR, "kcbrain_review.jsonl")
MAX_PER_RUN = 12  # 実測1件4〜10秒。過去分の初回バックフィルも数日で追いつく


def main():
    journal = jsonl_load(JOURNAL_PATH)
    done = {r["trade_id"] for r in jsonl_load(REVIEW_PATH)}
    pending = [j for j in journal if j["trade_id"] not in done]
    if not pending:
        print("kcbrain review: nothing to do")
        return

    import re
    for entry in pending[:MAX_PER_RUN]:
        symbol = entry["pair"].replace("/", "_").upper()
        if not re.fullmatch(r"[A-Z0-9]{2,12}_[A-Z0-9]{2,12}", symbol):
            # kcbrainのsymbol検証に通らない銘柄はスキップ扱いで記録し、毎時間の再試行を防ぐ
            jsonl_append(REVIEW_PATH, {
                "trade_id": entry["trade_id"], "pair": entry["pair"],
                "skipped": "symbol not accepted by kcbrain"})
            continue
        payload = {
            "symbol": symbol,
            "timeframe": "M5",
            "as_of": entry.get("reviewed_at", ""),
            "position": {
                "side": "long",
                "opened_at": entry.get("open_date"),
                "held_hours": entry.get("held_hours"),
                "result_pct": entry.get("result_pct"),
                "mae_pct": entry.get("mae_pct"),
                "mfe_pct": entry.get("mfe_pct"),
                "exit_reason": entry.get("exit_reason"),
                "pre_entry_change_4h_pct": entry.get("pre_entry_chg_4h_pct"),
            },
            "news": [entry["recent_news"]] if entry.get("recent_news") else [],
            "question": (
                "This closed spot long trade came from an ML strategy (FreqAI) on MEXC. "
                "Review the entry/exit process quality using only the supplied numbers."
            ),
        }
        try:
            resp = kcbrain_client.post("/v1/review/trade", payload, timeout=300)
        except Exception as exc:
            print("kcbrain review failed for trade %s: %s" % (entry["trade_id"], exc))
            continue
        record = {
            "trade_id": entry["trade_id"],
            "pair": entry["pair"],
            "reviewed_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "result_pct": entry.get("result_pct"),
            "exit_reason": entry.get("exit_reason"),
            "kcbrain": resp.get("result"),
            "latency_ms": resp.get("latency_ms"),
            # 自家製プロンプト(postmortem)の評決をA/B用に併記
            "postmortem": {
                "category": entry.get("category"),
                "verdict": entry.get("verdict"),
                "lesson": entry.get("lesson"),
            },
        }
        jsonl_append(REVIEW_PATH, record)
        print("kcbrain review ok: trade %s %s -> %s" % (
            entry["trade_id"], entry["pair"],
            (resp.get("result") or {}).get("process_quality")))


if __name__ == "__main__":
    main()
