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
ARENA_AGENTS = [
    {"agent": "arena1", "port": 18325, "label": "baseline",
     "strategy": "KfreqaiVariantRebalance", "desc": "本番同等(統制)"},
    {"agent": "arena2", "port": 18329, "label": "giveback",
     "strategy": "KfreqaiVariantGiveback", "desc": "nofx由来ピーク割れクローズ"},
    {"agent": "arena3", "port": 18330, "label": "kcbraingate",
     "strategy": "KfreqaiVariantKcbraingate", "desc": "kcbrain判断ゲート(OI/funding証拠)"},
]
ARENA_BUDGET_USDT = 2000
ARENA_SLOTS = 3
ARENA_DD_SUSPEND_PCT = 10  # kfxaiと同じ表示基準(閉損益が予算の-10%で停止扱い)


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
        row.update({"budget_usdt": ARENA_BUDGET_USDT, "max_open_trades": ARENA_SLOTS})
        try:
            profit = _ft_get(meta["port"], "profit", auth)
            openpos = _ft_get(meta["port"], "status", auth)
            daily = _ft_get(meta["port"], "daily?timescale=1", auth)
            closed_pnl = float(profit.get("profit_closed_coin") or 0)
            trades = int(profit.get("closed_trade_count") or profit.get("trade_count") or 0)
            wins = int(profit.get("winning_trades") or 0)
            row.update({
                "status": "suspended" if closed_pnl <= -ARENA_BUDGET_USDT * ARENA_DD_SUSPEND_PCT / 100
                          else "active",
                "trades": trades,
                "wins": wins,
                "win_rate": round(wins / trades, 3) if trades else None,
                "pnl_usdt": round(closed_pnl, 2),
                "equity_usdt": round(ARENA_BUDGET_USDT + closed_pnl, 2),
                "return_pct": round(100 * closed_pnl / ARENA_BUDGET_USDT, 3),
                "open_now": len(openpos or []),
                "open_profit_usdt": round(sum(float(t.get("profit_abs") or 0)
                                              for t in (openpos or [])), 2),
                "today_pnl_usdt": float((daily.get("data") or [{}])[0].get("abs_profit") or 0),
            })
        except Exception as exc:
            row.update({"status": "offline", "error": str(exc)[:120]})
        agents.append(row)
    return {"ok": True, "budget_usdt": ARENA_BUDGET_USDT, "slots": ARENA_SLOTS,
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
