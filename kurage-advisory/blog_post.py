#!/usr/bin/env python3
"""Kurage AI trading blog poster. Runs 3x/day (05:00/13:00/21:00 JST),
piggybacking on the same schedule as daily_directive.py.

Claude CLI (OAuth session, same mechanism as daily_directive.py) writes a
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
import json
import os
import re
import smtplib
import ssl
import subprocess
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
from hourly_regime import load_config, fetch_ohlcv, build_stats_block  # noqa: E402
from daily_directive import resolve_claude_bin  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLUDIT_CREDS_PATH = os.path.join(BASE_DIR, "user_data", "blog-bludit-admin.txt")
BLUDIT_BASE = os.environ.get("KFREQAI_BLUDIT_BASE", "https://kurage.exbridge.jp/blog")
CLAUDE_MODEL = os.environ.get("KFREQAI_CLAUDE_MODEL", "sonnet")
CLAUDE_TIMEOUT = int(os.environ.get("KFREQAI_CLAUDE_TIMEOUT", "240"))

JST = timezone(timedelta(hours=9))

DISCLOSURE_FOOTER = (
    "\n\n---\n\n"
    "**注記**: このbotは現在dry-run（ペーパートレード）で稼働しており、実際の資金は一切動いていません。"
    "本記事の損益・取引はすべてシミュレーション上の数値です。"
    "取引ダッシュボード: https://kurage.exbridge.jp/kfreqai.php?view=summary"
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
    stats_block = build_stats_block(ohlcv_by_pair)

    state = advisory_state.read_state()
    history = advisory_state.read_recent_history(max_entries=24 if is_daily_summary else 8)

    return {
        "status": status,
        "balance": balance,
        "profit": profit,
        "trades": trades,
        "daily": daily,
        "stats_block": stats_block,
        "advisory_state": state,
        "history": history,
        "strategy": config["exchange"]["name"],
    }


def build_prompt(ctx, is_daily_summary):
    open_positions = len(ctx["status"])
    recent_trades = ctx["trades"][:8]
    trades_lines = []
    for t in recent_trades:
        pair = t.get("pair", "?")
        profit_pct = t.get("close_profit")
        reason = t.get("exit_reason", "-")
        close_date = t.get("close_date", "-")
        if profit_pct is not None:
            trades_lines.append(f"{close_date}: {pair} {profit_pct*100:+.2f}% ({reason})")
    trades_block = "\n".join(trades_lines) or "（直近の約定なし）"

    history_lines = []
    for h in ctx["history"]:
        history_lines.append(f"{h.get('updated_at_iso', '?')} [{h.get('type')}] {h.get('value')} - {h.get('note', '')}")
    history_block = "\n".join(history_lines) or "（記録なし）"

    regime = ctx["advisory_state"].get("regime", {})
    directive = ctx["advisory_state"].get("directive", {})

    balance_total = ctx["balance"].get("total")
    profit_pct = ctx["profit"].get("profit_all_percent")
    winrate = ctx["profit"].get("winrate")

    kind = "1日の総括（21時）" if is_daily_summary else "定時の市況チェック（5時/13時）"

    return f"""あなたはKurageプロジェクトのAI暗号資産自動取引botの「中の人」として、ブログ記事を書くAIです。
今回の記事種別: {kind}

# 使えるデータ

現在の残高（シミュレーション）: {balance_total} USDT
累計損益: {profit_pct}%（勝率 {winrate}）
保有中ポジション数: {open_positions}

直近の約定履歴:
{trades_block}

主要銘柄の直近の価格変化率:
{ctx['stats_block']}

AI地合い判定（gemma4、毎時）: {regime.get('value', '不明')} - {regime.get('note', '')}
リスク方針（Claude、1日3回）: {directive.get('value', '不明')} - {directive.get('note', '')}

直近の判定履歴:
{history_block}

# 執筆ルール

- 日本語で、暗号資産に詳しい個人ブロガーのような自然な文体で書く
- タイトルは40字以内、具体的で興味を引くものにする
- 本文はMarkdown形式で400〜700字程度（総括記事なら700〜1000字程度）
- 上記データにない事実は絶対に創作しない。数値は与えられたものだけを使う
- **このbotは紙上取引(dry-run)であり、実際の資金は動いていないことを本文中でも自然に触れる**
- 煽り・投資助言・断定的な将来予測はしない（「〜する可能性がある」程度の書き方に留める）
{"- 総括記事なので、今日1日の値動き・判定推移・取引結果を振り返る構成にする" if is_daily_summary else "- 短めの定時レポートとして、現在の地合いと注目ポイントを簡潔にまとめる"}

# 出力形式（この3行の見出し以外、余計な文章を書かない）

TITLE: <タイトル>
SLUG: <URLに使う英語スラッグ。小文字・ハイフン区切り・3〜6単語>
---
<Markdown本文>
"""


def call_claude(prompt):
    claude_bin = resolve_claude_bin()
    if not claude_bin:
        raise RuntimeError("claude binary not found")
    cmd = [claude_bin, "-p", "--output-format", "text", "--tools", "", "--model", CLAUDE_MODEL, prompt]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"claude cli returncode={result.returncode} detail={detail[:300]!r}")
    return result.stdout.strip()


def parse_response(text):
    title_match = re.search(r"^TITLE:\s*(.+)$", text, re.MULTILINE)
    slug_match = re.search(r"^SLUG:\s*(.+)$", text, re.MULTILINE)
    if not title_match:
        raise RuntimeError("could not parse TITLE from claude output")
    title = title_match.group(1).strip()
    raw_slug = slug_match.group(1).strip() if slug_match else ""
    slug = re.sub(r"[^a-z0-9-]", "", raw_slug.lower().replace(" ", "-"))[:60] or "kfreqai-update"

    body_split = text.split("---", 1)
    body = body_split[1].strip() if len(body_split) > 1 else text
    return title, slug, body


def post_to_bludit(title, slug, body):
    creds = load_bludit_creds()
    now = datetime.now(JST)
    unique_slug = f"{slug}-{now.strftime('%Y%m%d-%H%M')}"
    resp = requests.post(
        f"{BLUDIT_BASE}/api/pages",
        data={
            "token": creds["BLUDIT_API_TOKEN"],
            "authentication": creds["BLUDIT_AUTH_TOKEN"],
            "title": title,
            "slug": unique_slug,
            "content": body,
            "status": "published",
            "tags": "AI自動取引,暗号資産,kfreqai",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    permalink = data.get("data", {}).get("permalink", f"{BLUDIT_BASE}/{unique_slug}")
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


def crosspost_email(title, body_markdown):
    hatena_to = os.environ.get("HATENA_POST_EMAIL", "")
    blogger_to = os.environ.get("BLOGGER_POST_EMAIL", "")
    if hatena_to:
        send_mail(title, body_markdown, hatena_to)
        print(f"[blog_post] crossposted to Hatena: {title}", flush=True)
        time.sleep(3)
    if blogger_to:
        send_mail(title, body_markdown, blogger_to)
        print(f"[blog_post] crossposted to Blogger: {title}", flush=True)


def main():
    now = datetime.now(JST)
    is_daily_summary = now.hour >= 18  # 21:00 JST run (allow slack for RandomizedDelaySec)

    ctx = gather_context(is_daily_summary)
    prompt = build_prompt(ctx, is_daily_summary)

    response_text = call_claude(prompt)
    title, slug, body = parse_response(response_text)
    body_with_footer = body + DISCLOSURE_FOOTER

    unique_slug, permalink = post_to_bludit(title, slug, body_with_footer)
    print(f"[blog_post] posted: {title} -> {permalink}", flush=True)

    if is_daily_summary:
        crosspost_email(title, body_with_footer)


if __name__ == "__main__":
    main()
