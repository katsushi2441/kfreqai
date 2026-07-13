#!/usr/bin/env python3
"""③トレード反省会ループ — クローズした取引を1件ずつ検死し、教訓を蓄積する。

毎時実行。未レビューのクローズ済みトレードについて:
1. 実データで文脈を再構成(エントリー前4hの騰落、保有中のMAE/MFE、決済理由)
2. 判定バックエンド(judgment_backend.py)が構造化された教訓(カテゴリ+一言教訓)を書く
3. user_data/lab/trade_journal.jsonl に追記

蓄積された教訓は①research_daily.pyが週次仮説の材料として読む。
2026-07-10のTAC事故調査(手動)を自動化したもの。数値の計算はコード側で行い、
判定バックエンドには解釈だけをさせる(数字の捏造余地を与えない)。
"""
import datetime
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_common import (BASE_DIR, LAB_DIR, ensure_lab_dir, jsonl_append,
                        jsonl_load, fetch_ohlcv_via_container)
import judgment_backend

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
    """エントリー前後の実データ文脈を計算する(判定バックエンドには計算させない)。
    汎用的なMAE/MFE計算なので判定ロジックの秘匿対象ではなく、ここに直接置く。"""
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


def review_with_llm(t, ctx):
    try:
        return judgment_backend.review_trade(t, ctx)
    except Exception as exc:
        print(f"[postmortem] review_trade failed: {exc}", flush=True)
        return {"category": "other", "verdict": "unknown", "lesson": "(判定失敗)"}


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
            review = review_with_llm(t, ctx)
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
