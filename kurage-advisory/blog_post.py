#!/usr/bin/env python3
"""Kurage AI trading blog poster. Runs 3x/day (05:00/13:00/21:00 JST),
piggybacking on the same schedule as daily_directive.py.

Asks the active judgment backend (see judgment_backend.py) to write a
short Japanese blog post about the bot's current market read and (at
21:00) a daily recap, which gets published to the Bludit blog at
https://kurage.exbridge.jp/blog/. The 21:00 run additionally cross-posts
via email to Hatena Blog and Blogger, matching vwork's proven
posting-by-email mechanism (aixec/.env SMTP + *_POST_EMAIL addresses).

Hard rule: this bot trades in dry-run (paper trading, no real capital).
The blog must never imply real money is at stake -- a fixed disclosure
footer is appended in code (not left to the model) so it can't be
dropped by a bad generation.
"""
import os
import re
import smtplib
import ssl
import sys
import time
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText

import markdown
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "user_data", "strategies"),
)
import advisory_state  # noqa: E402
import judgment_backend  # noqa: E402
from hourly_regime import load_config, fetch_ohlcv  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLUDIT_CREDS_PATH = os.path.join(BASE_DIR, "user_data", "blog-bludit-admin.txt")
BLUDIT_BASE = os.environ.get("KFREQAI_BLUDIT_BASE", "https://kurage.exbridge.jp/blog")

JST = timezone(timedelta(hours=9))

DISCLOSURE_FOOTER = (
    "\n\n---\n\n"
    "**注記**: このbotは現在dry-run（ペーパートレード）で稼働しており、実際の資金は一切動いていません。"
    "本記事の損益・取引はすべてシミュレーション上の数値です。"
    "[取引ダッシュボードはこちら](https://kurage.exbridge.jp/kfreqai.php?view=summary)"
)


def load_bludit_creds():
    creds = {}
    with open(BLUDIT_CREDS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "=" not in line:
                continue
            k, v = line.split("=", 1)
            creds[k] = v
    return creds


def freqtrade_base_and_token():
    config = load_config()
    api = config["api_server"]
    base = f"http://127.0.0.1:{api['listen_port']}"
    resp = requests.post(
        f"{base}/api/v1/token/login", auth=(api["username"], api["password"]), timeout=10
    )
    resp.raise_for_status()
    return base, resp.json()["access_token"]


def freqtrade_get(base, token, path):
    resp = requests.get(f"{base}{path}", headers={"Authorization": f"Bearer {token}"}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def gather_context(is_daily_summary):
    base, token = freqtrade_base_and_token()
    status = freqtrade_get(base, token, "/api/v1/status")
    balance = freqtrade_get(base, token, "/api/v1/balance")
    profit = freqtrade_get(base, token, "/api/v1/profit")
    trades_resp = freqtrade_get(base, token, "/api/v1/trades?limit=15&order_by_id=false")
    trades = trades_resp.get("trades", [])
    daily_resp = freqtrade_get(
        base, token, "/api/v1/daily?timescale=" + ("2" if is_daily_summary else "1")
    )
    daily = daily_resp.get("data", [])

    config = load_config()
    exchange_name = config["exchange"]["name"]
    pairs = config["exchange"]["pair_whitelist"]
    ohlcv_by_pair = fetch_ohlcv(exchange_name, pairs)

    state = advisory_state.read_state()
    history = advisory_state.read_recent_history(max_entries=24 if is_daily_summary else 8)

    return {
        "status": status,
        "balance": balance,
        "profit": profit,
        "trades": trades,
        "daily": daily,
        "ohlcv_by_pair": ohlcv_by_pair,
        "advisory_state": state,
        "history": history,
        "strategy": config["exchange"]["name"],
    }


def _ftp_env():
    """FTP認証情報。systemdはEnvironmentFile=aixec/.envで注入するが、
    手動実行でも動くよう未設定ならファイルから読む。"""
    if os.environ.get("FTP_HOST"):
        return {k: os.environ[k] for k in ("FTP_HOST", "FTP_USER", "FTP_PASS")}
    env = {}
    try:
        with open("/home/kojima/work/aixec/.env", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k] = v
    except OSError:
        return None
    if all(k in env for k in ("FTP_HOST", "FTP_USER", "FTP_PASS")):
        return env
    return None


def upload_ogp_image(unique_slug, title):
    """記事タイトルからOGP画像を生成しblogのuploadsへFTP格納。
    失敗しても投稿は続行する(fail-open)。戻り値はcoverImage用ファイル名かNone。"""
    try:
        import ftplib
        import io as _io

        import blog_ogp
        png = blog_ogp.generate(title)
        env = _ftp_env()
        if not env:
            print("[blog] OGP: FTP認証情報なし、デフォルト画像のまま")
            return None
        filename = f"ogp-{unique_slug}.png"
        with ftplib.FTP(env["FTP_HOST"], timeout=60) as ftp:
            ftp.login(env["FTP_USER"], env["FTP_PASS"])
            ftp.storbinary(
                f"STOR /web/kurage_exbridge_jp/blog/bl-content/uploads/{filename}",
                _io.BytesIO(png))
        # 素のファイル名だとBluditがページUUID配下(uploads/pages/<uuid>/)を探して
        # 404になる。フルURLならcoverImage()がそのまま返すので、フラットな
        # uploads直下+フルURL指定で運用する(2026-07-14実測)。
        return f"{BLUDIT_BASE}/bl-content/uploads/{filename}"
    except Exception as exc:
        print(f"[blog] OGP生成/アップロード失敗(投稿は続行): {str(exc)[:120]}")
        return None


def update_blog_sitemap():
    """全公開記事からsitemap.xmlを生成してブログ直下へFTP配置する。
    Bluditのsitemapプラグインが無効なため自前生成(2026-07-14 SEO監査)。
    失敗しても投稿処理は続行する。"""
    try:
        import ftplib
        import io as _io
        from xml.sax.saxutils import escape

        creds = load_bludit_creds()
        env = _ftp_env()
        if not env:
            return False
        r = requests.get(f"{BLUDIT_BASE}/api/pages",
                         params={"token": creds["BLUDIT_API_TOKEN"], "number": 500}, timeout=30)
        pages = r.json().get("data", [])
        rows = [f"<url><loc>{escape(BLUDIT_BASE)}/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>"]
        for p in pages:
            loc = escape(p.get("permalink") or f"{BLUDIT_BASE}/{p['key']}")
            lastmod = (p.get("dateRaw") or "")[:10]
            lm = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
            rows.append(f"<url><loc>{loc}</loc>{lm}<priority>0.8</priority></url>")
        xml = ('<?xml version="1.0" encoding="UTF-8"?>'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               + "".join(rows) + "</urlset>")
        with ftplib.FTP(env["FTP_HOST"], timeout=60) as ftp:
            ftp.login(env["FTP_USER"], env["FTP_PASS"])
            ftp.storbinary("STOR /web/kurage_exbridge_jp/blog/sitemap.xml",
                           _io.BytesIO(xml.encode("utf-8")))
        return True
    except Exception as exc:
        print(f"[blog] sitemap更新失敗(続行): {str(exc)[:120]}")
        return False


def post_to_bludit(title, slug, body):
    creds = load_bludit_creds()
    now = datetime.now(JST)
    unique_slug = f"{slug}-{now.strftime('%Y%m%d-%H%M')}"
    payload = {
        "token": creds["BLUDIT_API_TOKEN"],
        "authentication": creds["BLUDIT_AUTH_TOKEN"],
        "title": title,
        "slug": unique_slug,
        "content": body,
        "status": "published",
        "tags": "AI自動取引,暗号資産,kfreqai",
    }
    cover = upload_ogp_image(unique_slug, title)
    if cover:
        payload["coverImage"] = cover
    resp = requests.post(f"{BLUDIT_BASE}/api/pages", data=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    permalink = data.get("data", {}).get("permalink", f"{BLUDIT_BASE}/{unique_slug}")
    update_blog_sitemap()
    return unique_slug, permalink


def send_mail(title, body_markdown, to_addr):
    smtp_host = os.environ.get("SMTP_HOST", "mail18.heteml.jp")
    smtp_port = int(os.environ.get("SMTP_PORT", 465))
    smtp_from = os.environ["SMTP_FROM"]
    smtp_pass = os.environ["SMTP_PASSWORD"]

    html_body = markdown.markdown(body_markdown, extensions=["extra"])
    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = title
    msg["From"] = smtp_from
    msg["To"] = to_addr

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx) as s:
        s.login(smtp_from, smtp_pass)
        s.sendmail(smtp_from, [to_addr], msg.as_bytes())


def satellite_variant(channel, title, permalink, body_markdown):
    """元記事と別内容の衛星記事をバックエンドに書かせる。失敗はNone(原文転載へフォールバック)。"""
    return judgment_backend.write_satellite_article(channel, title, permalink, body_markdown)


def crosspost_email(title, body_markdown, permalink):
    """21時の総括をはてな/Bloggerへ配信。可能なら衛星記事(別内容+バックリンク)、
    生成に失敗したチャネルは原文転載(リンク付き)にフォールバックする。"""
    targets = {"hatena": os.environ.get("HATENA_POST_EMAIL", ""),
               "blogger": os.environ.get("BLOGGER_POST_EMAIL", "")}
    backlink = f"\n\n---\n\nより詳しいデータは元記事をどうぞ: [{title}]({permalink})"
    for channel, to_addr in targets.items():
        if not to_addr:
            continue
        variant = None
        try:
            variant = satellite_variant(channel, title, permalink, body_markdown)
        except Exception as exc:
            print(f"[blog_post] satellite {channel} failed: {exc}", flush=True)
        if variant:
            send_mail(variant["title"], variant["body"] + backlink, to_addr)
            print(f"[blog_post] satellite crossposted to {channel}: {variant['title']}", flush=True)
        else:
            send_mail(title, body_markdown + backlink, to_addr)
            print(f"[blog_post] verbatim crossposted to {channel}: {title}", flush=True)
        time.sleep(3)


def main():
    now = datetime.now(JST)
    is_daily_summary = now.hour >= 18  # 21:00 JST run (allow slack for RandomizedDelaySec)

    # 成長ループのSEOターゲット(キューが空ならNone→従来通り)
    seo_topic = None
    try:
        sys.path.insert(0, os.path.join(BASE_DIR, "kurage-growth"))
        from growth_loop import pop_topic
        seo_topic = pop_topic()
        if seo_topic:
            print(f"[blog_post] SEO target: {seo_topic['query']}", flush=True)
    except Exception as exc:
        print(f"[blog_post] growth topic unavailable: {exc}", flush=True)

    ctx = gather_context(is_daily_summary)

    try:
        article = judgment_backend.write_blog_article(ctx, is_daily_summary, seo_topic)
    except Exception as exc:
        print(f"[blog_post] write_blog_article failed entirely: {exc}", flush=True)
        return
    if not article:
        print("[blog_post] backend returned nothing, skipping post", flush=True)
        return

    body_with_footer = article["body"] + DISCLOSURE_FOOTER
    unique_slug, permalink = post_to_bludit(article["title"], article["slug"], body_with_footer)
    print(f"[blog_post] posted: {article['title']} -> {permalink}", flush=True)

    if is_daily_summary:
        crosspost_email(article["title"], body_with_footer, permalink)


if __name__ == "__main__":
    main()
