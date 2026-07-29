"""FastAPI service exposing user_data/advisory_state.json (regime/directive
judgement) for the dashboard.

kfreqai.php (kurage_web) runs on a separate host (heteml) and can't read
this host's files directly. freqtrade's own REST API (18313) doesn't know
about this custom advisory concept and offers no supported plugin point for
adding routes to it without forking upstream (see project discussion,
2026-07-13), so this is a small standalone API instead.

No new externally-facing port: binds 127.0.0.1 only. It's reached through
the already-public 18314 nginx server via a path (see
/etc/nginx/conf.d/kfreqai-18314.conf, location /advisory-state), which
already forwards FreqUI traffic to 18313 -- this just adds one more path
proxied to this process instead of freqtrade.
"""
import os
import datetime
import json
import sys

from fastapi import FastAPI

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_BASE, "user_data", "strategies"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import advisory_state  # noqa: E402
import market_facts  # noqa: E402

NEWS_SIGNALS_PATH = os.path.join(_BASE, "user_data", "news_signals.json")

app = FastAPI()


@app.get("/advisory-state")
def get_advisory_state():
    """regime/directiveに加えmarket_facts(銘柄ニュース+ブロック)と
    arena(戦略エージェントアリーナのリーダーボード)を同梱して返す。
    nginx(18314)は location = /advisory-state の完全一致プロキシなので、
    サブパスを増やさず1つの応答に相乗りさせる(sudo無しで済む設計判断)。"""
    state = advisory_state.read_state()
    state["market_facts"] = get_market_facts()
    state["arena"] = get_arena()
    return state


# ---- 戦略エージェントアリーナ(dry-run) 2026-07-17 ----
# 各エージェントはdocker-compose.override.ymlの独立freqtradeコンテナ。
# ここはローカルREST APIを集約するだけ(認証情報はconfig_agent1.jsonから読む)。
# port=現物, short_port=先物ショート機。残高・損益は必ず両方を合算する
# (資金は現物10000+先物10000=各20000。個別ダッシュボード kfreqai.php と同じ集計)。
ARENA_AGENTS = [
    {"agent": "arena1", "port": 18325, "short_port": 18344, "slot": "A", "label": "baseline",
     "strategy": "KfreqaiVariantRebalance", "desc": "本番同等(統制)"},
    {"agent": "arena2", "port": 18329, "short_port": 18345, "slot": "B", "label": "trend-1h",
     "strategy": "KfreqaiTrendStrategy", "desc": "1hブレイク追随+ピークトレール(検証済+9.75%/18mo)"},
    {"agent": "arena3", "port": 18330, "short_port": 18346, "slot": "C", "label": "meanrev-1h",
     "strategy": "KfreqaiMeanRevStrategy", "desc": "1h押し目買い/反発売り(検証済+6.06%/18mo)"},
]
ARENA_DD_SUSPEND_PCT = 10  # kfxaiと同じ表示基準(閉損益が予算の-10%で停止扱い)


def _agent_config(agent_name):
    """予算(dry_run_wallet)と枠(max_open_trades)は各エージェントの実configを単一の真実源
    として読む。資金は現物config_agentN + 先物config_futures_short_arenaN の合算(=各20000)。
    表示に金額をハードコードしない(不一致事故の防止)。"""
    n = "".join(ch for ch in agent_name if ch.isdigit())
    budget = 0.0
    slots = 0
    found = False
    for fn in ("config_agent%s.json" % n, "config_futures_short_arena%s.json" % n):
        try:
            c = json.load(open(os.path.join(_BASE, "user_data", fn)))
            budget += float(c.get("dry_run_wallet") or 0)
            slots += int(c.get("max_open_trades") or 0)
            found = True
        except Exception:
            pass
    if not found:
        return {"budget_usdt": None, "max_open_trades": None}
    return {"budget_usdt": budget, "max_open_trades": slots}


def _short_auth_header(n):
    """先物ショート機(config_futures_short_arenaN.json)のBasic認証ヘッダ。"""
    import base64
    cfg = json.load(open(os.path.join(_BASE, "user_data",
                                      "config_futures_short_arena%s.json" % n)))
    creds = "%s:%s" % (cfg["api_server"]["username"], cfg["api_server"]["password"])
    return "Basic " + base64.b64encode(creds.encode()).decode()


def _ft_get(port, path, auth_header, timeout=2.5):
    import urllib.request
    req = urllib.request.Request(
        "http://127.0.0.1:%d/api/v1/%s" % (port, path),
        headers={"Authorization": auth_header})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _arena_auth_header():
    import base64
    cfg = json.load(open(os.path.join(_BASE, "user_data", "config_agent1.json")))
    creds = "%s:%s" % (cfg["api_server"]["username"], cfg["api_server"]["password"])
    return "Basic " + base64.b64encode(creds.encode()).decode()


@app.get("/arena")
def get_arena():
    """アリーナのリーダーボード(kfxaiのstrategy_performance相当)。"""
    try:
        auth = _arena_auth_header()
    except Exception as exc:
        return {"ok": False, "error": "config_agent1.json unreadable: %s" % exc}
    agents = []
    for meta in ARENA_AGENTS:
        row = dict(meta)
        acfg = _agent_config(meta["agent"])  # 実configから予算・枠(=単一の真実源)
        budget = acfg["budget_usdt"] or 0
        row.update(acfg)
        try:
            # 個別ダッシュボード(kfreqai.php)と同じく現物+先物ショートを合算する。
            n = "".join(ch for ch in meta["agent"] if ch.isdigit())
            legs = [(meta["port"], auth)]
            if meta.get("short_port"):
                try:
                    legs.append((meta["short_port"], _short_auth_header(n)))
                except Exception:
                    pass
            closed_pnl = 0.0
            trades = wins = losses = 0
            open_now = 0
            open_profit = 0.0
            today = 0.0
            # 本日損益: freqtradeの/dailyはUTC暦日単位で個別画面(JST集計)とズレるため
            # 使わず、約定履歴のclose_dateをJSTに直して当日分を合算する(kfreqai.phpと同式)
            jst = datetime.timezone(datetime.timedelta(hours=9))
            today_jst = datetime.datetime.now(jst).date()
            for port, ah in legs:
                profit = _ft_get(port, "profit", ah)
                openpos = _ft_get(port, "status", ah)
                closed_pnl += float(profit.get("profit_closed_coin") or 0)
                trades += int(profit.get("closed_trade_count") or profit.get("trade_count") or 0)
                wins += int(profit.get("winning_trades") or 0)
                losses += int(profit.get("losing_trades") or 0)
                open_now += len(openpos or [])
                open_profit += sum(float(t.get("profit_abs") or 0) for t in (openpos or []))
                tr = _ft_get(port, "trades?limit=2000&order_by_id=false", ah)
                for t in (tr.get("trades") or []):
                    if t.get("is_open") or not t.get("close_date"):
                        continue
                    try:
                        cd = datetime.datetime.fromisoformat(
                            str(t["close_date"]).replace("Z", "+00:00"))
                        if cd.tzinfo is None:
                            cd = cd.replace(tzinfo=datetime.timezone.utc)
                        if cd.astimezone(jst).date() == today_jst:
                            today += float(t.get("close_profit_abs") or 0)
                    except Exception:
                        continue
            row.update({
                "status": "suspended" if (budget and closed_pnl <= -budget * ARENA_DD_SUSPEND_PCT / 100)
                          else "active",
                "trades": trades,
                "wins": wins,
                # 口座ごとに分母が違い%は合成できないため、勝敗数を合算して勝率を再計算
                "win_rate": round(wins / (wins + losses), 3) if (wins + losses) else None,
                "pnl_usdt": round(closed_pnl, 2),
                "equity_usdt": round(budget + closed_pnl, 2),
                "return_pct": round(100 * closed_pnl / budget, 3) if budget else None,
                "open_now": open_now,
                "open_profit_usdt": round(open_profit, 2),
                "today_pnl_usdt": round(today, 2),
            })
        except Exception as exc:
            row.update({"status": "offline", "error": str(exc)[:120]})
        agents.append(row)
    # 全エージェントの予算が同一なら代表値を、違えばNone(=UI側は各行の値を使う)。
    budgets = {a.get("budget_usdt") for a in agents if a.get("budget_usdt")}
    common_budget = budgets.pop() if len(budgets) == 1 else None
    return {"ok": True, "budget_usdt": common_budget,
            "dd_suspend_pct": ARENA_DD_SUSPEND_PCT, "agents": agents,
            "updated_at": datetime.datetime.now().isoformat(timespec="seconds")}


@app.get("/advisory-state/market-facts")
def get_market_facts():
    """有効期限内の銘柄ニュース事実 + アクティブな24hブロック(ローカル直叩き用)。"""
    blocks = {}
    try:
        data = json.load(open(NEWS_SIGNALS_PATH))
        cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(hours=24)).isoformat()[:19]
        for sym, sig in (data.get("blocks") or {}).items():
            if sig.get("at", "") > cutoff:
                blocks[sym] = sig
    except Exception:
        pass

    facts = []
    try:
        conn = market_facts.connect()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()[:19]
        rows = conn.execute(
            "SELECT pair, raw_title, sentiment, event_type, confidence,"
            " observed_at, source_url FROM facts"
            " WHERE expires_at IS NULL OR expires_at > ?"
            " ORDER BY observed_at DESC LIMIT 30", (now,)).fetchall()
        facts = [dict(r) for r in rows]
    except Exception:
        pass
    return {"blocks": blocks, "facts": facts}
