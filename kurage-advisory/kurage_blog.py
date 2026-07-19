"""Kurageブログ(Bludit, kurage.exbridge.jp/blog)への共通投稿処理。

投稿の認証情報・OGP生成・sitemap更新はすべてkfreqai側(このディレクトリ)にあるため、
「ブログ投稿の持ち主」はkfreqai。kfreqai(暗号資産)・kfxai(FX)の双方が **一方向で**
このモジュールを使う(2026-07-17の『kfxaiはkfreqaiのブログ基盤を共用』方針に沿う)。
これによりkfreqai↔kfxaiの循環依存(両方存在しないと動かない状態)を避ける。
"""
from __future__ import annotations

import io
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

_DIR = os.path.dirname(os.path.abspath(__file__))
BLUDIT_CREDS_PATH = os.path.join(os.path.dirname(_DIR), "user_data", "blog-bludit-admin.txt")
BLUDIT_BASE = os.environ.get("KURAGE_BLUDIT_BASE", "https://kurage.exbridge.jp/blog")
JST = timezone(timedelta(hours=9))


def load_bludit_creds() -> dict:
    creds: dict[str, str] = {}
    with open(BLUDIT_CREDS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "=" not in line:
                continue
            k, v = line.split("=", 1)
            creds[k] = v
    return creds


def upload_ogp_image(unique_slug: str, title: str) -> str | None:
    """OGP画像を生成してBluditのuploadsへFTP格納。失敗しても投稿は続行(Noneを返す)。"""
    try:
        import ftplib

        sys.path.insert(0, _DIR)
        import blog_ogp  # noqa: PLC0415 (local to kfreqai)

        png = blog_ogp.generate(title)
        env = {k: os.environ.get(k, "") for k in ("FTP_HOST", "FTP_USER", "FTP_PASS")}
        if not all(env.values()):
            return None
        filename = f"ogp-{unique_slug}.png"
        with ftplib.FTP(env["FTP_HOST"], timeout=60) as ftp:
            ftp.login(env["FTP_USER"], env["FTP_PASS"])
            ftp.storbinary(
                f"STOR /web/kurage_exbridge_jp/blog/bl-content/uploads/{filename}",
                io.BytesIO(png))
        return f"{BLUDIT_BASE}/bl-content/uploads/{filename}"
    except Exception as exc:
        print(f"[blog] OGP生成/アップロード失敗(投稿は続行): {str(exc)[:120]}")
        return None


def post_to_bludit(title: str, slug: str, body: str,
                   tags: str = "", category: str | None = None,
                   footer: str = "") -> tuple[str, str]:
    """Bluditへ投稿。footerを渡すと本文末尾に付与。categoryは既存カテゴリ名(要事前作成)。"""
    creds = load_bludit_creds()
    now = datetime.now(JST)
    if footer:
        body = body + footer
    unique_slug = f"{slug}-{now.strftime('%Y%m%d-%H%M')}"
    payload = {
        "token": creds["BLUDIT_API_TOKEN"],
        "authentication": creds["BLUDIT_AUTH_TOKEN"],
        "title": title,
        "slug": unique_slug,
        "content": body,
        "status": "published",
        "tags": tags,
    }
    if category:
        payload["category"] = category
    cover = upload_ogp_image(unique_slug, title)
    if cover:
        payload["coverImage"] = cover
    resp = requests.post(f"{BLUDIT_BASE}/api/pages", data=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    permalink = data.get("data", {}).get("permalink", f"{BLUDIT_BASE}/{unique_slug}")
    try:
        sys.path.insert(0, _DIR)
        from blog_post import update_blog_sitemap  # noqa: PLC0415
        update_blog_sitemap()
    except Exception as exc:
        print(f"[blog] sitemap更新失敗(投稿は成功): {str(exc)[:120]}")
    return unique_slug, permalink
