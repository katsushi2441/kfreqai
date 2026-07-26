"""kfreqaihl(Hyperliquidマルチテナント版)のテナント台帳。

1テナント = 1 X(旧Twitter)アカウント。usernameをキーに、
・生成したAgent Wallet(取引専用鍵。出金権限なし)のアドレス/秘密鍵
・ユーザーが入力したメインアカウント(資金を置く側)のアドレス
・戦略パラメータの上書き値(hl_schemas.SCHEMASの範囲内)
を持つ。freqtrade側のように1インスタンス1台帳ではなく、複数ユーザー分の
行をこの1つのsqliteに持つのがマルチテナント設計の核心(前回合意した方式B)。

Agent Walletの秘密鍵はここに平文で保存する(user_data配下でgitignore対象)。
本番運用に進める前に、暗号化保管(例: 環境鍵でのAES-GCM)への切り替えが必要。
現段階はテストネット検証フェーズなのでこの制約を明記するに留める。
"""
import datetime
import json
import os
import sqlite3
import threading

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_BASE, "user_data", "hl_tenants.sqlite")

_lock = threading.Lock()


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS tenants (
        username TEXT PRIMARY KEY,
        agent_address TEXT NOT NULL,
        agent_private_key TEXT NOT NULL,
        main_wallet_address TEXT,
        agent_approved INTEGER NOT NULL DEFAULT 0,
        strategy TEXT NOT NULL DEFAULT 'HLTrendPerpStrategy',
        params_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    return conn


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def get_or_create(username, agent_wallet_factory):
    """username既存ならその行を、無ければagent_wallet_factory()で新規生成して返す。
    agent_wallet_factoryはhl_connector.generate_agent_walletを渡す想定
    (このモジュール自体はhyperliquid/eth_accountに依存させない=関心の分離)。"""
    with _lock:
        conn = _conn()
        row = conn.execute("SELECT * FROM tenants WHERE username=?", (username,)).fetchone()
        if row:
            conn.close()
            return dict(row)
        wallet = agent_wallet_factory()
        now = _now()
        conn.execute(
            "INSERT INTO tenants (username, agent_address, agent_private_key,"
            " main_wallet_address, agent_approved, strategy, params_json,"
            " created_at, updated_at) VALUES (?,?,?,NULL,0,?,?,?,?)",
            (username, wallet["address"], wallet["private_key"],
             "HLTrendPerpStrategy", "{}", now, now))
        conn.commit()
        row = conn.execute("SELECT * FROM tenants WHERE username=?", (username,)).fetchone()
        conn.close()
        return dict(row)


def set_main_wallet(username, address, approved=False):
    with _lock:
        conn = _conn()
        conn.execute(
            "UPDATE tenants SET main_wallet_address=?, agent_approved=?, updated_at=?"
            " WHERE username=?", (address, int(approved), _now(), username))
        conn.commit()
        conn.close()


def mark_approved(username):
    with _lock:
        conn = _conn()
        conn.execute("UPDATE tenants SET agent_approved=1, updated_at=? WHERE username=?",
                     (_now(), username))
        conn.commit()
        conn.close()


def get_params(username):
    conn = _conn()
    row = conn.execute("SELECT params_json FROM tenants WHERE username=?", (username,)).fetchone()
    conn.close()
    if not row:
        return {}
    try:
        return json.loads(row["params_json"] or "{}")
    except Exception:
        return {}


def set_params(username, params):
    with _lock:
        conn = _conn()
        conn.execute("UPDATE tenants SET params_json=?, updated_at=? WHERE username=?",
                     (json.dumps(params, ensure_ascii=False), _now(), username))
        conn.commit()
        conn.close()


def list_active_tenants():
    """発注ループ用: メインウォレットが登録済み(=Agent Wallet委任が完了しうる)テナント一覧。"""
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM tenants WHERE main_wallet_address IS NOT NULL AND agent_approved=1"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- ポジション状態(ピークPnLトレール用。kfreqaiのself._peakに相当) -----------
def _pos_conn():
    conn = _conn()
    conn.execute("""CREATE TABLE IF NOT EXISTS position_state (
        username TEXT NOT NULL,
        coin TEXT NOT NULL,
        peak_profit REAL NOT NULL DEFAULT 0,
        opened_at TEXT,
        PRIMARY KEY (username, coin)
    )""")
    return conn


def get_peak(username, coin):
    conn = _pos_conn()
    row = conn.execute("SELECT peak_profit FROM position_state WHERE username=? AND coin=?",
                       (username, coin)).fetchone()
    conn.close()
    return float(row["peak_profit"]) if row else 0.0


def update_peak(username, coin, current_profit):
    """現在のピークを更新して返す(max運用)。"""
    with _lock:
        conn = _pos_conn()
        row = conn.execute("SELECT peak_profit FROM position_state WHERE username=? AND coin=?",
                           (username, coin)).fetchone()
        peak = max(float(row["peak_profit"]) if row else 0.0, float(current_profit))
        conn.execute("INSERT INTO position_state (username, coin, peak_profit, opened_at)"
                     " VALUES (?,?,?,?) ON CONFLICT(username, coin) DO UPDATE SET peak_profit=?",
                     (username, coin, peak, _now(), peak))
        conn.commit()
        conn.close()
        return peak


def clear_peak(username, coin):
    with _lock:
        conn = _pos_conn()
        conn.execute("DELETE FROM position_state WHERE username=? AND coin=?", (username, coin))
        conn.commit()
        conn.close()
