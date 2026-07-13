#!/usr/bin/env python3
"""③トレード反省会ループ — クローズした取引をgemma4が1件ずつ検死し、教訓を蓄積する。

毎時実行。未レビューのクローズ済みトレードについて:
1. 実データで文脈を再構成(エントリー前4hの騰落、保有中のMAE/MFE、決済理由)
2. gemma4が構造化された教訓(カテゴリ+一言教訓)をJSONで書く
3. user_data/lab/trade_journal.jsonl に追記

蓄積された教訓は①research_daily.pyが週次仮説の材料として読む。
2026-07-10のTAC事故調査(手動)を自動化したもの。数値の計算はコード側で行い、
LLMには解釈だけをさせる(数字の捏造余地を与えない)。
"""
import datetime
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_common import (BASE_DIR, LAB_DIR, ensure_lab_dir, jsonl_append,
                        jsonl_load, ollama_generate, extract_json,
                        fetch_ohlcv_via_container)
import judgment_logic

DB_PATH = os.path.join(BASE_DIR, "user_data", "tradesv3.sqlite")
JOURNAL_PATH = os.path.join(LAB_DIR, "trade_journal.jsonl")


def reviewed_ids():
    return {r["trade_id"] for r in jsonl_load(JOURNAL_PATH)}


def closed_trades(conn):
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT id, pair, open_date, close_date, open_rate, close_rate,"
        " close_profit, exit_reason, enter_tag FROM trades"
        " WHERE is_open=0 ORDER BY id").fetchall()


def build_context(t):
    return judgment_logic.build_postmortem_context(t, fetch_ohlcv_via_container)


def review_with_llm(ctx):
    prompt = judgment_logic.build_postmortem_prompt(ctx)
    raw = ollama_generate(prompt, num_predict=300, temperature=0.1)
    parsed = extract_json(raw)
    if not isinstance(parsed, dict) or parsed.get("category") not in judgment_logic.TRADE_POSTMORTEM_CATEGORIES:
        return {"category": "other", "verdict": "unknown",
                "lesson": "(LLM出力のパース失敗)", "raw": raw[:200]}
    return parsed


def main():
    ensure_lab_dir()
    done = reviewed_ids()
    conn = sqlite3.connect(DB_PATH)
    targets = [t for t in closed_trades(conn) if t["id"] not in done]
    if not targets:
        print("[postmortem] 新規クローズなし")
        return
    for t in targets[:20]:  # 1回の実行で最大20件(初回の大量処理を分散)
        try:
            ctx = build_context(t)
            review = review_with_llm(ctx)
            jsonl_append(JOURNAL_PATH, {
                "trade_id": t["id"],
                "reviewed_at": datetime.datetime.now(datetime.timezone.utc).isoformat()[:19],
                **ctx, **review,
            })
            print(f"[postmortem] #{t['id']} {t['pair']} {ctx['result_pct']:+.1f}% "
                  f"→ {review['category']} / {review['lesson']}")
        except Exception as e:
            print(f"[postmortem] #{t['id']} {t['pair']} 失敗: {str(e)[:120]}")


if __name__ == "__main__":
    main()
