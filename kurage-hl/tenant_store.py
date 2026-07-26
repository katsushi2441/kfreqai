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


# --- ペーパートレード(実弾を使わない仮想売買。FXの先行体験用) --------------------
# 資金もウォレット委任も不要。usernameだけで仮想口座を持てる(アンバサダーが手軽に
# 開始できるように)。約定はエンジンがmainnetの実価格でシミュレーションし、建玉・
# 損益をこのDBに持つ。marketで市場を分ける(今は 'fx'。将来cryptoペーパーも同じ表)。
def _paper_conn():
    conn = _conn()
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_accounts (
        username TEXT NOT NULL,
        market TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        starting_equity REAL NOT NULL DEFAULT 1000,
        realized_pnl REAL NOT NULL DEFAULT 0,
        payer_wallet TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (username, market)
    )""")
    # 既存テーブルへの後方互換マイグレーション(payer_wallet=x402支払い用ウォレット)。
    try:
        conn.execute("ALTER TABLE paper_accounts ADD COLUMN payer_wallet TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # 既に列がある
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_positions (
        username TEXT NOT NULL,
        market TEXT NOT NULL,
        coin TEXT NOT NULL,
        is_short INTEGER NOT NULL DEFAULT 0,
        entry_px REAL NOT NULL,
        size REAL NOT NULL,
        notional REAL NOT NULL,
        peak REAL NOT NULL DEFAULT 0,
        opened_at TEXT NOT NULL,
        PRIMARY KEY (username, market, coin)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_fills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        market TEXT NOT NULL,
        coin TEXT NOT NULL,
        side TEXT NOT NULL,
        px REAL NOT NULL,
        size REAL NOT NULL,
        pnl_usd REAL NOT NULL DEFAULT 0,
        reason TEXT,
        ts INTEGER NOT NULL
    )""")
    return conn


def paper_get_account(username, market="fx"):
    conn = _paper_conn()
    row = conn.execute("SELECT * FROM paper_accounts WHERE username=? AND market=?",
                       (username, market)).fetchone()
    conn.close()
    return dict(row) if row else None


def paper_enable(username, market="fx", starting_equity=1000.0, payer_wallet=None):
    """ペーパー口座を作成/有効化(冪等)。既存があればenabled=1に戻すだけ(残高は保持)。
    payer_wallet=x402(AI利用料)の支払いに使うウォレット。渡されたら更新する
    (取引の委任ではなく、支払い用の接続アドレス)。"""
    with _lock:
        conn = _paper_conn()
        now = _now()
        row = conn.execute("SELECT username FROM paper_accounts WHERE username=? AND market=?",
                           (username, market)).fetchone()
        if row:
            if payer_wallet:
                conn.execute("UPDATE paper_accounts SET enabled=1, payer_wallet=?, updated_at=?"
                             " WHERE username=? AND market=?", (payer_wallet, now, username, market))
            else:
                conn.execute("UPDATE paper_accounts SET enabled=1, updated_at=? WHERE username=? AND market=?",
                             (now, username, market))
        else:
            conn.execute("INSERT INTO paper_accounts (username, market, enabled, starting_equity,"
                         " realized_pnl, payer_wallet, created_at, updated_at) VALUES (?,?,1,?,0,?,?,?)",
                         (username, market, float(starting_equity), payer_wallet, now, now))
        conn.commit()
        conn.close()
    return paper_get_account(username, market)


def paper_reset(username, market="fx", starting_equity=1000.0):
    """口座を初期化: 建玉・約定履歴を消し、確定損益を0に、残高を初期値へ。"""
    with _lock:
        conn = _paper_conn()
        now = _now()
        conn.execute("DELETE FROM paper_positions WHERE username=? AND market=?", (username, market))
        conn.execute("DELETE FROM paper_fills WHERE username=? AND market=?", (username, market))
        conn.execute("UPDATE paper_accounts SET realized_pnl=0, starting_equity=?, enabled=1,"
                     " updated_at=? WHERE username=? AND market=?",
                     (float(starting_equity), now, username, market))
        conn.commit()
        conn.close()
    return paper_get_account(username, market)


def paper_set_enabled(username, market, enabled):
    with _lock:
        conn = _paper_conn()
        conn.execute("UPDATE paper_accounts SET enabled=?, updated_at=? WHERE username=? AND market=?",
                     (1 if enabled else 0, _now(), username, market))
        conn.commit()
        conn.close()


def paper_list_enabled(market="fx"):
    conn = _paper_conn()
    rows = conn.execute("SELECT * FROM paper_accounts WHERE market=? AND enabled=1", (market,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def paper_add_realized(username, market, delta):
    with _lock:
        conn = _paper_conn()
        conn.execute("UPDATE paper_accounts SET realized_pnl=realized_pnl+?, updated_at=?"
                     " WHERE username=? AND market=?", (float(delta), _now(), username, market))
        conn.commit()
        conn.close()


def paper_get_positions(username, market="fx"):
    conn = _paper_conn()
    rows = conn.execute("SELECT * FROM paper_positions WHERE username=? AND market=?",
                        (username, market)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def paper_open_position(username, market, coin, is_short, entry_px, size, notional):
    with _lock:
        conn = _paper_conn()
        conn.execute("INSERT OR REPLACE INTO paper_positions (username, market, coin, is_short,"
                     " entry_px, size, notional, peak, opened_at) VALUES (?,?,?,?,?,?,?,0,?)",
                     (username, market, coin, 1 if is_short else 0, float(entry_px),
                      float(size), float(notional), _now()))
        conn.commit()
        conn.close()


def paper_update_position_peak(username, market, coin, peak):
    with _lock:
        conn = _paper_conn()
        conn.execute("UPDATE paper_positions SET peak=? WHERE username=? AND market=? AND coin=?",
                     (float(peak), username, market, coin))
        conn.commit()
        conn.close()


def paper_close_position(username, market, coin):
    with _lock:
        conn = _paper_conn()
        conn.execute("DELETE FROM paper_positions WHERE username=? AND market=? AND coin=?",
                     (username, market, coin))
        conn.commit()
        conn.close()


def paper_add_fill(username, market, coin, side, px, size, pnl_usd, reason, ts):
    with _lock:
        conn = _paper_conn()
        conn.execute("INSERT INTO paper_fills (username, market, coin, side, px, size, pnl_usd,"
                     " reason, ts) VALUES (?,?,?,?,?,?,?,?,?)",
                     (username, market, coin, side, float(px), float(size), float(pnl_usd),
                      reason, int(ts)))
        conn.commit()
        conn.close()


def paper_recent_fills(username, market="fx", limit=50):
    conn = _paper_conn()
    rows = conn.execute("SELECT * FROM paper_fills WHERE username=? AND market=?"
                        " ORDER BY ts DESC LIMIT ?", (username, market, int(limit))).fetchall()
    conn.close()
    return [dict(r) for r in rows]
