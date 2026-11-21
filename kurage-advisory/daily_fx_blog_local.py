#!/usr/bin/env python3
"""FXデイリーブログを、同じサーバーの kfxbrain を直接呼んで投稿する（暗号資産ブログと同じ形）。

記事の組み立て・投稿は公開版 kfxai/scripts/daily_fx_blog.py をそのまま使い、
kfxbrain を呼ぶところ（call_kfxbrain）だけを、Bankr x402 の有料窓口から直接呼び出しに差し替える。
2026-07-31 に kfxai が x402 専用になってから、財布の鍵が無くて 7/30 を最後に止まっていた（2026-11-21）。

  /usr/bin/python3 daily_fx_blog_local.py [--dry-run]
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kfxbrain_client  # noqa: E402

FX_BLOG = "/home/kojima/work/kfxai/scripts/daily_fx_blog.py"
spec = importlib.util.spec_from_file_location("daily_fx_blog", FX_BLOG)
blog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(blog)


def call_kfxbrain_local(pairs):
    # 公開版と同じ問い（質問文・時間足）を、x402 を通さずに投げる
    payload = {
        "timeframe": "H1",
        "as_of": blog.datetime.now(blog.JST).isoformat(timespec="seconds"),
        "pairs": pairs,
        "question": "今後4〜24時間の観点でこれらの主要FXペアを機会順にランキングしてください。"
                    "各ペアのdrivers(根拠)とrisks(リスク)、market_summaryはすべて日本語で、"
                    "1ペアあたり2項目以内の簡潔な日本語で書いてください。"
                    "JSON構造・キー名・symbol(pair)・direction は英語のまま。",
    }
    return kfxbrain_client.post("/v1/market/opportunity-ranking", payload, timeout=540)


blog.call_kfxbrain = call_kfxbrain_local

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(FX_BLOG)))
    sys.exit(blog.main())
