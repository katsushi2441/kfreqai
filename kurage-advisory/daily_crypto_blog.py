#!/usr/bin/env python3
"""暗号資産デイリーブログ(kcbrain判断)を Kurageブログ(Bludit)へ1日1回投稿する。

素材は kcbrain(Kurage Crypto Brain, :18328)が実データ(価格/OI/funding)から出した
判断(kcbrain_shadow.json: opportunity-ranking + anomaly)。これを読みやすい日本語記事に
整形し、カテゴリ/タグ kcbrain・暗号資産 で投稿。末尾に KAIC(kaic.php)への誘導を付ける。

  --dry-run  投稿せず本文を標準出力に出すだけ(品質確認用)

kfxブログ(daily_fx_blog.py)と対になる。認証・OGPはkfreqai/kfxaiの実装を共用。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHADOW_PATH = os.path.join(BASE_DIR, "user_data", "kcbrain_shadow.json")
JST = timezone(timedelta(hours=9))

CATEGORY = "kcbrain"
TAGS = "kcbrain,暗号資産,AI判断"
KAIC_URL = "https://kurage.exbridge.jp/kaic.php"

DISCLOSURE = (
    "\n\n---\n\n"
    "**この記事について**: 上記は [Kurage Crypto Brain (kcbrain)](https://kurage.exbridge.jp/kcbrain.php) が、"
    "各銘柄の価格・出来高・建玉(OI)・funding rateといった実データだけを根拠に出した構造化判断です。"
    "ローカルLLM(Gemma 4)で動き、複数のオープンソース・トレーディング知能を組み合わせています。"
    "市場データの自動取得や注文執行は行いません。**投資助言ではありません。** "
    "暗号資産は価格変動が大きく、元本を失う可能性があります。最終判断はご自身で行ってください。\n\n"
    f"🧭 **今日のAI投資委員会の3つの判断（暗号資産・FX・Polymarket）と、7日後の答え合わせは → [Kurage AI Investment Committee (KAIC)]({KAIC_URL})**\n\n"
    "関連: [kcbrain ワークベンチ](https://kurage.exbridge.jp/kcbrain.php) / "
    "[Kurage FX Brain](https://kfxbrain.exbridge.jp) / "
    "[Kurage FreqAI Trade](https://kurage.exbridge.jp/kfreqai.php)"
)

DIR_JP = {"long": "強気(ロング候補)", "short": "弱気(ショート候補)",
          "watch": "様子見", "avoid": "回避"}
SEV_JP = {"low": "小", "medium": "中", "high": "大", "critical": "重大"}


def load_shadow() -> dict:
    with open(SHADOW_PATH, encoding="utf-8") as f:
        return json.load(f)


def sym(s: str) -> str:
    return (s or "").replace("_USDT", "").replace("_", "/")


def build_article(shadow: dict) -> tuple[str, str, str]:
    now = datetime.now(JST)
    date_s = now.strftime("%Y年%-m月%-d日")
    rank = (shadow.get("opportunity_ranking") or {}).get("ranking") or []
    summary = (shadow.get("opportunity_ranking") or {}).get("market_summary") or ""
    anomalies = (shadow.get("anomaly") or {}).get("anomalies") or []
    top = rank[0] if rank else None

    if top:
        title = f"{date_s}の暗号資産AI判断：kcbrainの本命は{sym(top['symbol'])}（{DIR_JP.get(top.get('direction'),'—')}）"
    else:
        title = f"{date_s}の暗号資産AI判断（kcbrain）"

    lines = [f"# {title}", ""]
    lines.append(
        f"Kurage Crypto Brain（kcbrain）が{now.strftime('%-m月%-d日 %H時')}時点の実データ"
        "（価格・出来高・建玉・funding rate）だけを根拠に、暗号資産の機会と異常を判定しました。"
        "以下はローカルLLMが出した構造化判断をそのまま整理したものです。\n")

    if summary:
        lines.append("## 今日の地合い（kcbrainの要約）\n")
        lines.append(f"> {summary}\n")

    if rank:
        lines.append("## 機会ランキング（上位）\n")
        lines.append("| 順位 | 銘柄 | 方向 | スコア | 主な根拠 |")
        lines.append("|---:|---|---|---:|---|")
        for e in rank[:6]:
            drv = "、".join(e.get("drivers", [])[:2]) or "—"
            lines.append(
                f"| {e.get('rank')} | {sym(e.get('symbol'))} | "
                f"{DIR_JP.get(e.get('direction'),'—')} | {e.get('score')} | {drv} |")
        lines.append("")
        top_risks = (top.get("risks") or []) if top else []
        if top_risks:
            lines.append(
                f"本命の{sym(top['symbol'])}について、kcbrainは次のリスクも挙げています："
                + "、".join(top_risks[:3]) + "。\n")

    if anomalies:
        lines.append("## 異常検知（要注意）\n")
        for a in anomalies[:5]:
            lines.append(
                f"- **{sym(a.get('symbol'))}**：{a.get('type')} の異常"
                f"（深刻度 {SEV_JP.get(a.get('severity'), a.get('severity'))}）"
                + (f" — {a.get('evidence',[''])[0]}" if a.get("evidence") else ""))
        lines.append("")

    lines.append("## この判断の読み方\n")
    lines.append(
        "スコアが高い＝必ず上がる、ではありません。kcbrainは「与えられた実データの範囲で、"
        "リスク調整後にどの銘柄が相対的に機会があるか」を順位づけしているだけで、"
        "将来価格を保証するものではありません。異常検知に挙がった銘柄は、"
        "急なOI/価格変化が起きているサインなので、順張りでもポジションサイズに注意してください。\n")

    body = "\n".join(lines) + DISCLOSURE
    slug = f"crypto-kcbrain-{now.strftime('%Y%m%d')}"
    return title, slug, body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="投稿せず本文を表示")
    args = ap.parse_args()

    shadow = load_shadow()
    # 素材の鮮度チェック(6時間より古ければ中止)
    at = shadow.get("at", "")
    try:
        age_h = (datetime.now(JST) - datetime.fromisoformat(at)).total_seconds() / 3600
        if age_h > 6:
            print(f"[crypto-blog] kcbrain_shadowが古い({age_h:.1f}h)。投稿を中止。")
            return 1
    except Exception:
        pass

    title, slug, body = build_article(shadow)
    if args.dry_run:
        print(f"# [DRY-RUN] title: {title}\n# slug: {slug}\n# category: {CATEGORY} tags: {TAGS}\n")
        print(body)
        return 0

    # 投稿はkfreqai自身のブログ基盤(kurage_blog)を使う。kfxaiには一切依存しない。
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import kurage_blog  # noqa: PLC0415
    _, permalink = kurage_blog.post_to_bludit(title, slug, body, tags=TAGS, category=CATEGORY)
    print(f"posted: {permalink}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
