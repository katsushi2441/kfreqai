#!/usr/bin/env python3
"""Kurage AI取引ブログの成長ループ — 日次tick (AIKnowledgeCMSのMEASURE移植)。

aiknowledgecms で実証した growth loop を kurage.exbridge.jp/blog/ 向けに
小さく移植したもの:

  SENSE   — GSC(kurage.exbridge.jp)から /blog/ ページ別成績・ブログ経由クエリ・
            サイト全体の暗号資産/AI取引関連クエリを観測して growth.sqlite に蓄積
  TRIAGE  — 追跡クエリの順位下落を検出して alerts に積む
  TOPICS  — 「表示はあるがクリックが少ない」「2ページ目(11-20位)」のクエリを
            SEOターゲットとして topics キューへ (blog_post.py が消費する)
  REPORT  — 成績サマリJSONを heteml へFTP公開し、kfreqai.php ダッシュボードが表示

ブログはまだGSCデータがほぼ無いため、当面はサイト全体のPILLAR_KEYWORDS
関連クエリだけが topics に入る。ブログのインデックスが進むと
ページ別成績・ブログ経由クエリ・リライト判断の材料が自動的に増えていく。

認証: aiknowledgecms/adapters/sensors/gsc.py と同じ gcloud ADC 方式。
"""
from __future__ import annotations

import ftplib
import io
import json
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "user_data" / "growth.sqlite"
ENV_FILE = Path("/home/kojima/work/aixec/.env")
ADC_PATH = Path.home() / ".config/gcloud/application_default_credentials.json"

SITE = "https://kurage.exbridge.jp/"
BLOG_PREFIX = "/blog/"
REMOTE_JSON = "/web/kurage_exbridge_jp/kfreqai-growth.json"

# ブログの主題に関係するクエリだけをサイト全体から採掘する
PILLAR_KEYWORDS = [
    "仮想通貨", "暗号資産", "ビットコイン", "btc", "イーサリアム", "eth",
    "crypto", "自動売買", "自動取引", "トレード", "bot", "freqtrade",
    "ai投資", "ai 投資", "ai トレード", "ペーパートレード", "dry-run",
]

POSITION_DROP_THRESHOLD = 5.0
TRACK_MIN_IMPRESSIONS = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS page_metrics (
  date TEXT NOT NULL, slug TEXT NOT NULL,
  impressions INTEGER, clicks INTEGER, ctr REAL, position REAL,
  PRIMARY KEY (date, slug)
);
CREATE TABLE IF NOT EXISTS query_metrics (
  date TEXT NOT NULL, query TEXT NOT NULL, scope TEXT NOT NULL,  -- blog / site
  impressions INTEGER, clicks INTEGER, position REAL,
  PRIMARY KEY (date, query, scope)
);
CREATE TABLE IF NOT EXISTS topics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  query TEXT NOT NULL UNIQUE,
  reason TEXT,
  score REAL NOT NULL DEFAULT 0,
  used INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL, detail TEXT,
  created_at TEXT NOT NULL
);
"""


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _load_env() -> dict:
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    return env


def _gsc_token() -> tuple[str, str] | None:
    proc = subprocess.run(
        ["gcloud", "auth", "application-default", "print-access-token"],
        capture_output=True, text=True)
    token = proc.stdout.strip()
    if proc.returncode != 0 or not token:
        return None
    qp = ""
    if ADC_PATH.exists():
        qp = json.loads(ADC_PATH.read_text()).get("quota_project_id", "")
    return token, qp


def _gsc_query(token: str, qp: str, payload: dict) -> list:
    url = ("https://www.googleapis.com/webmasters/v3/sites/"
           + urllib.parse.quote(SITE, safe="") + "/searchAnalytics/query")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if qp:
        headers["X-Goog-User-Project"] = qp
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read()).get("rows", [])


def sense(conn) -> dict:
    auth = _gsc_token()
    if auth is None:
        raise RuntimeError("gcloud ADC token unavailable")
    token, qp = auth
    end = time.strftime("%Y-%m-%d", time.localtime(time.time() - 2 * 86400))
    start = time.strftime("%Y-%m-%d", time.localtime(time.time() - 30 * 86400))
    today = time.strftime("%Y-%m-%d")
    base = {"startDate": start, "endDate": end, "rowLimit": 200}
    blog_filter = {"dimensionFilterGroups": [{"filters": [
        {"dimension": "page", "operator": "contains", "expression": BLOG_PREFIX}]}]}

    # (1) /blog/ ページ別成績
    pages = _gsc_query(token, qp, {**base, "dimensions": ["page"], **blog_filter})
    blog_pages = []
    for r in pages:
        slug = r["keys"][0].rstrip("/").rsplit("/", 1)[-1] or "(top)"
        blog_pages.append({"slug": slug, "impressions": r["impressions"],
                           "clicks": r["clicks"], "ctr": round(r.get("ctr", 0), 4),
                           "position": round(r.get("position", 0), 1)})
        conn.execute(
            "INSERT OR REPLACE INTO page_metrics VALUES (?,?,?,?,?,?)",
            (today, slug, r["impressions"], r["clicks"],
             round(r.get("ctr", 0), 4), round(r.get("position", 0), 1)))

    # (2) ブログ経由クエリ(順位追跡・下落検知の対象)
    bq = _gsc_query(token, qp, {**base, "dimensions": ["query"], **blog_filter})
    blog_queries = []
    for r in bq:
        q = r["keys"][0]
        cur_pos = round(r.get("position", 0), 1)
        blog_queries.append({"query": q, "impressions": r["impressions"],
                             "clicks": r["clicks"], "position": cur_pos})
        if r["impressions"] >= TRACK_MIN_IMPRESSIONS:
            prev = conn.execute(
                "SELECT position FROM query_metrics WHERE query=? AND scope='blog'"
                " AND date < ? ORDER BY date DESC LIMIT 1", (q, today)).fetchone()
            if prev and cur_pos - prev["position"] >= POSITION_DROP_THRESHOLD:
                conn.execute(
                    "INSERT INTO alerts (kind, detail, created_at) VALUES (?,?,?)",
                    ("pos_drop", json.dumps(
                        {"query": q, "prev": prev["position"], "cur": cur_pos},
                        ensure_ascii=False), now()))
        conn.execute(
            "INSERT OR REPLACE INTO query_metrics VALUES (?,?,?,?,?,?)",
            (today, q, "blog", r["impressions"], r["clicks"], cur_pos))

    # (3) サイト全体からブログ主題に関係するクエリを採掘 → topics
    sq = _gsc_query(token, qp, {**base, "dimensions": ["query"], "rowLimit": 500})
    topics_added = 0
    for r in sq:
        q = r["keys"][0]
        ql = q.lower()
        if not any(k in ql for k in PILLAR_KEYWORDS):
            continue
        if len(q) > 40 or any(t in q for t in ("site:", '"', "“", "”")):
            continue
        conn.execute(
            "INSERT OR REPLACE INTO query_metrics VALUES (?,?,?,?,?,?)",
            (today, q, "site", r["impressions"], r["clicks"],
             round(r.get("position", 0), 1)))
        pos = r.get("position", 99)
        low_ctr = r["impressions"] >= 10 and r.get("ctr", 0) < 0.02
        page2 = 11 <= pos <= 20 and r["impressions"] >= 5
        if not (low_ctr or page2):
            continue
        reason = (f"GSC 28日間: 表示{r['impressions']}回・クリック{r['clicks']}回・"
                  f"平均順位{pos:.1f}。" +
                  ("2ページ目→あと一押しで1ページ目。" if page2 else "表示はあるのに低CTR。"))
        score = (12.0 if page2 else 10.0) + r["impressions"] / 100
        try:
            conn.execute(
                "INSERT INTO topics (query, reason, score, created_at) VALUES (?,?,?,?)",
                (q, reason, score, now()))
            topics_added += 1
        except sqlite3.IntegrityError:
            pass  # 既出クエリ
    conn.commit()
    return {"blog_pages": blog_pages, "blog_queries": blog_queries,
            "topics_added": topics_added}


def build_summary(conn, sensed: dict) -> dict:
    topics = [dict(r) for r in conn.execute(
        "SELECT query, reason, score FROM topics WHERE used=0"
        " ORDER BY score DESC LIMIT 5")]
    alerts = [dict(r) for r in conn.execute(
        "SELECT kind, detail, created_at FROM alerts ORDER BY id DESC LIMIT 5")]
    return {
        "updated_at": now(),
        "blog_pages": sorted(sensed["blog_pages"],
                             key=lambda p: -p["impressions"])[:10],
        "blog_queries": sorted(sensed["blog_queries"],
                               key=lambda q: -q["impressions"])[:10],
        "next_topics": topics,
        "alerts": alerts,
        "note": "GSC 28日間の実測。dry-run(紙上取引)ブログの検索成績です。",
    }


def publish_json(summary: dict) -> None:
    env = _load_env()
    data = json.dumps(summary, ensure_ascii=False, indent=1).encode("utf-8")
    with ftplib.FTP(env["FTP_HOST"], timeout=60) as ftp:
        ftp.login(env["FTP_USER"], env["FTP_PASS"])
        ftp.storbinary(f"STOR {REMOTE_JSON}", io.BytesIO(data))


def pop_topic() -> dict | None:
    """blog_post.py から呼ぶ: 未使用の最優先SEOターゲットを1件取り出して消費する。"""
    if not DB_PATH.exists():
        return None
    conn = connect()
    row = conn.execute(
        "SELECT id, query, reason FROM topics WHERE used=0"
        " ORDER BY score DESC LIMIT 1").fetchone()
    if row is None:
        conn.close()
        return None
    conn.execute("UPDATE topics SET used=1 WHERE id=?", (row["id"],))
    conn.commit()
    conn.close()
    return {"query": row["query"], "reason": row["reason"]}


def main():
    conn = connect()
    sensed = sense(conn)
    summary = build_summary(conn, sensed)
    publish_json(summary)
    print(f"[growth_loop] {now()} blog_pages={len(sensed['blog_pages'])}"
          f" blog_queries={len(sensed['blog_queries'])}"
          f" topics_added={sensed['topics_added']}"
          f" queue={len(summary['next_topics'])}", flush=True)


if __name__ == "__main__":
    main()
