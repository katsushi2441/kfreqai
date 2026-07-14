"""market_facts — 銘柄別の市場事実(ニュース・イベント)の蓄積層。

ユーザー方針(2026-07-14): AgentReach等で集めた情報は「1事実=1行」で
SQLiteに蓄積し、既存のLLM判断ポイント(postmortem/regime)から検索して使う。
フレームワーク(Graphiti/Mem0等)は事実が数千件を超えて関係クエリが
必要になってから検討する。expires_atを最初から持たせ、時限性のある
事実(ハッキング・上場廃止等)が自然に失効するようにしておく。

運用はシャドー第一: 取引ゲートには直結させず、まず判断材料への添付のみ。
昇格は実効果の検証後(2026-07-10の教訓: 検証なしの材料をライブに直結させない)。
"""
import datetime
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_common import LAB_DIR, ensure_lab_dir

DB_PATH = os.path.join(LAB_DIR, "market_facts.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    pair TEXT NOT NULL,             -- 'BONK' や市場全体は 'MARKET'
    fact TEXT NOT NULL,             -- 1行の事実(日本語)
    event_type TEXT,                -- listing/hack/delisting/regulatory/other...
    sentiment TEXT,                 -- positive/negative/neutral
    confidence REAL,                -- 0-1
    source_url TEXT,
    observed_at TEXT NOT NULL,      -- ISO8601 UTC
    expires_at TEXT,                -- これ以降は判断材料に使わない(NULL=無期限)
    raw_title TEXT
);
CREATE INDEX IF NOT EXISTS idx_facts_pair ON facts(pair, observed_at);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    fact, raw_title, content=facts, content_rowid=id);
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, fact, raw_title)
    VALUES (new.id, new.fact, new.raw_title);
END;
"""


def connect():
    ensure_lab_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()[:19]


def add_fact(conn, pair, fact, event_type=None, sentiment=None, confidence=None,
             source_url=None, ttl_hours=72, raw_title=None):
    """事実を1件追加。既に同一(pair, fact)があれば重複追加しない。"""
    dup = conn.execute(
        "SELECT 1 FROM facts WHERE pair=? AND fact=? LIMIT 1", (pair, fact)).fetchone()
    if dup:
        return False
    now = datetime.datetime.now(datetime.timezone.utc)
    expires = (now + datetime.timedelta(hours=ttl_hours)).isoformat()[:19] if ttl_hours else None
    conn.execute(
        "INSERT INTO facts (pair, fact, event_type, sentiment, confidence,"
        " source_url, observed_at, expires_at, raw_title)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (pair, fact, event_type, sentiment, confidence, source_url,
         now.isoformat()[:19], expires, raw_title))
    conn.commit()
    return True


def valid_facts(conn, pair, limit=5):
    """有効期限内の事実を新しい順に返す(pair='MARKET'で市場全体)。"""
    now = _now_iso()
    rows = conn.execute(
        "SELECT * FROM facts WHERE pair=? AND (expires_at IS NULL OR expires_at > ?)"
        " ORDER BY observed_at DESC LIMIT ?", (pair, now, limit)).fetchall()
    return [dict(r) for r in rows]


def facts_summary_for_prompt(conn, pair, limit=3):
    """判断プロンプトに添付する短い箇条書き。無ければ空文字。"""
    rows = valid_facts(conn, pair, limit)
    if not rows:
        return ""
    lines = []
    for r in rows:
        conf = f"(確度{r['confidence']:.1f})" if r["confidence"] is not None else ""
        lines.append(f"- [{r['observed_at'][:10]}] {r['fact']} {conf}".rstrip())
    return "\n".join(lines)


def stats(conn):
    total = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    valid = conn.execute(
        "SELECT COUNT(*) FROM facts WHERE expires_at IS NULL OR expires_at > ?",
        (_now_iso(),)).fetchone()[0]
    pairs = conn.execute("SELECT COUNT(DISTINCT pair) FROM facts").fetchone()[0]
    return {"total": total, "valid": valid, "pairs": pairs}


if __name__ == "__main__":
    conn = connect()
    print(json.dumps(stats(conn), ensure_ascii=False))
