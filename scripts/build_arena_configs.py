#!/usr/bin/env python3
"""戦略エージェントアリーナ(dry-run)のconfigを生成する。

本番config(user_data/config.json, gitignored)を土台に、エージェント別の
上書きを適用して user_data/config_agent{1,2,3}.json を書き出す。
configには秘密情報(jwt/password)が含まれるため生成物もgitignore対象。

アリーナ設計(2026-07-17):
- 各エージェント: 予算 dry_run_wallet=2000 USDT(約30万円) / 枠 max_open_trades=3
- ペアは全エージェント共通: 本番whitelist(129)のうちMEXC 24h出来高上位80
  (エージェント間の公平比較のため同一セット・生成時に固定)
- agent1 = ベースライン統制: 本番と同じ KfreqaiVariantRebalance(比較の基準)
- agent2 = nofx由来: KfreqaiVariantGiveback(ピーク割れクローズ)
- agent3 = kcbrain判断ゲート: KfreqaiVariantKcbraingate(OI/funding証拠のLLM判断)
- APIポートは採番時にss -ltn実測で確認した18300番台の空き(18325/18329/18330)

再生成: python3 scripts/build_arena_configs.py
(本番configの変更を取り込みたい時に再実行。identifierとペアは維持される)
"""
import copy
import json
import os
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "user_data", "config.json")
PAIRS_CACHE = os.path.join(BASE_DIR, "user_data", "arena_pairs.json")

AGENTS = [
    {"n": 1, "port": 18325, "identifier": "arena1-rebalance",
     "name": "baseline", "strategy": "KfreqaiVariantRebalance"},
    {"n": 2, "port": 18329, "identifier": "arena2-giveback",
     "name": "giveback", "strategy": "KfreqaiVariantGiveback"},
    {"n": 3, "port": 18330, "identifier": "arena3-kcbraingate",
     "name": "kcbraingate", "strategy": "KfreqaiVariantKcbraingate"},
]
BUDGET_USDT = 2000
SLOTS = 3
N_PAIRS = 80


def top_pairs_by_volume(whitelist):
    """MEXC公開tickerで24h quoteVolume上位N_PAIRSを選ぶ。一度選んだら
    arena_pairs.jsonに固定(比較期間中にペア母集団を変えない)。"""
    if os.path.exists(PAIRS_CACHE):
        with open(PAIRS_CACHE, encoding="utf-8") as f:
            return json.load(f)["pairs"]
    with urllib.request.urlopen(
            "https://api.mexc.com/api/v3/ticker/24hr", timeout=30) as resp:
        tickers = json.loads(resp.read().decode())
    vol = {t["symbol"]: float(t.get("quoteVolume") or 0) for t in tickers}
    ranked = sorted(whitelist, key=lambda p: vol.get(p.replace("/", ""), 0), reverse=True)
    pairs = ranked[:N_PAIRS]
    with open(PAIRS_CACHE, "w", encoding="utf-8") as f:
        json.dump({"pairs": pairs, "note": "arena共通ペア(選定時24h出来高上位・固定)"},
                  f, ensure_ascii=False, indent=1)
    return pairs


def main():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        base = json.load(f)
    pairs = top_pairs_by_volume(base["exchange"]["pair_whitelist"])
    for agent in AGENTS:
        cfg = copy.deepcopy(base)
        cfg["bot_name"] = "kfreqai-arena%d-%s" % (agent["n"], agent["name"])
        cfg["dry_run"] = True
        cfg["dry_run_wallet"] = BUDGET_USDT
        cfg["max_open_trades"] = SLOTS
        cfg["exchange"]["pair_whitelist"] = pairs
        cfg["api_server"]["listen_port"] = agent["port"]
        cfg["freqai"]["identifier"] = agent["identifier"]
        out = os.path.join(BASE_DIR, "user_data", "config_agent%d.json" % agent["n"])
        with open(out, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print("wrote %s (port %d, %s, %d pairs)" % (
            out, agent["port"], agent["strategy"], len(pairs)))


if __name__ == "__main__":
    main()
