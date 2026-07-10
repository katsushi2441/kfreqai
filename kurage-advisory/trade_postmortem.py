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
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_common import (BASE_DIR, LAB_DIR, ensure_lab_dir, jsonl_append,
                        jsonl_load, ollama_generate, extract_json,
                        fetch_ohlcv_via_container)

DB_PATH = os.path.join(BASE_DIR, "user_data", "tradesv3.sqlite")
JOURNAL_PATH = os.path.join(LAB_DIR, "trade_journal.jsonl")

CATEGORIES = ["pump_chase", "flash_crash", "normal_roi", "early_exit",
              "stale_loser", "whipsaw", "other"]


def reviewed_ids():
    return {r["trade_id"] for r in jsonl_load(JOURNAL_PATH)}


def closed_trades(conn):
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT id, pair, open_date, close_date, open_rate, close_rate,"
        " close_profit, exit_reason, enter_tag FROM trades"
        " WHERE is_open=0 ORDER BY id").fetchall()


def build_context(t):
    """エントリー前後の実データ文脈を計算する(LLMには計算させない)。"""
    od = datetime.datetime.fromisoformat(t["open_date"]).replace(tzinfo=datetime.timezone.utc)
    cd = datetime.datetime.fromisoformat(t["close_date"]).replace(tzinfo=datetime.timezone.utc)
    pre_since = int((od - datetime.timedelta(hours=4)).timestamp() * 1000)
    pre = [c for c in fetch_ohlcv_via_container(t["pair"], "5m", pre_since, 60)
           if c[0] < od.timestamp() * 1000]
    pre_chg = (t["open_rate"] / pre[0][1] - 1) * 100 if pre else None

    n_candles = min(500, max(2, int((cd - od).total_seconds() / 300) + 2))
    hold = [c for c in fetch_ohlcv_via_container(
        t["pair"], "5m", int(od.timestamp() * 1000), n_candles)
        if c[0] <= cd.timestamp() * 1000 + 300000]
    mae = (min(c[3] for c in hold) / t["open_rate"] - 1) * 100 if hold else None
    mfe = (max(c[2] for c in hold) / t["open_rate"] - 1) * 100 if hold else None
    return {
        "pair": t["pair"],
        "open_date": t["open_date"][:16],
        "held_hours": round((cd - od).total_seconds() / 3600, 1),
        "pre_entry_chg_4h_pct": round(pre_chg, 2) if pre_chg is not None else None,
        "mae_pct": round(mae, 2) if mae is not None else None,
        "mfe_pct": round(mfe, 2) if mfe is not None else None,
        "result_pct": round(t["close_profit"] * 100, 2),
        "exit_reason": t["exit_reason"],
    }


def review_with_llm(ctx):
    prompt = f"""あなたは暗号資産の自動売買ボットのトレード検死官です。
以下は実測データです(数値は全て検証済みの事実。あなたは解釈だけを行う)。

{json.dumps(ctx, ensure_ascii=False, indent=2)}

用語: pre_entry_chg_4h_pct=エントリー直前4時間の騰落率,
MAE=保有中の最大逆行, MFE=保有中の最大順行。

このトレードを次のJSONだけで評価してください(他の文章は一切書かない):
{{"category": "{'/'.join(CATEGORIES)}のいずれか1つ",
 "verdict": "good_entry/bad_entry/unavoidable のいずれか",
 "lesson": "40文字以内の日本語の教訓(このデータから言えることだけ)"}}
"""
    raw = ollama_generate(prompt, num_predict=300, temperature=0.1)
    parsed = extract_json(raw)
    if not isinstance(parsed, dict) or parsed.get("category") not in CATEGORIES:
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
