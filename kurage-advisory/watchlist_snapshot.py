#!/usr/bin/env python3
"""軽量ウォッチャー — 取引whitelistに入れずに、候補銘柄の値動きだけを日次記録する。

CASHCAT/USDTのように上場直後でFreqAIの学習に必要な30日分のデータが
まだ無い銘柄を、取引パイプライン(pair_whitelist)に入れずに観測するための
スクリプト。ccxtはkfreqaiコンテナ内にしかインストールされていないため、
hourly_regime.pyと同じ「docker exec経由でccxtを叩く」方式を踏襲する。

日次でuser_data/watchlist配下にJSONLで1行追記する。30日分たまったら
WATCH_MIN_DAYS_FOR_TRADEの目安に達した旨をログに出す(pair_whitelistへの
昇格は引き続き人間の判断で行う。自動追加はしない)。
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "user_data", "config.json")
WATCHLIST_DIR = os.path.join(BASE_DIR, "user_data", "watchlist")
CONTAINER_NAME = "kfreqai"

# 取引whitelistには入れず、ここで観測だけする銘柄。
WATCH_PAIRS = ["CASHCAT/USDT"]

# FreqAIのtrain_period_daysと同じ基準(config.jsonのfreqai.train_period_days)。
WATCH_MIN_DAYS_FOR_TRADE = 30


def load_exchange_name():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["exchange"]["name"]


def fetch_tickers(exchange_name, pairs):
    """kfreqaiコンテナ内のccxtでティッカーを取得する(ホストにはccxt未インストール)。"""
    script = f"""
import ccxt, json
ex = getattr(ccxt, {exchange_name!r})()
out = {{}}
for pair in {pairs!r}:
    try:
        t = ex.fetch_ticker(pair)
        out[pair] = {{"last": t.get("last"), "quoteVolume": t.get("quoteVolume"),
                      "percentage": t.get("percentage")}}
    except Exception as e:
        out[pair] = {{"error": str(e)}}
print(json.dumps(out))
"""
    result = subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "python3", "-c", script],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError("ccxt fetch failed: %s" % (result.stderr or result.stdout)[-500:])
    return json.loads(result.stdout)


def snapshot_path(pair):
    fname = pair.replace("/", "_") + ".jsonl"
    return os.path.join(WATCHLIST_DIR, fname)


def append_snapshot(pair, data):
    os.makedirs(WATCHLIST_DIR, exist_ok=True)
    now = datetime.now(timezone.utc)
    row = {
        "date": now.strftime("%Y-%m-%d"),
        "timestamp": now.isoformat(),
        **data,
    }
    path = snapshot_path(pair)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def count_days(pair):
    path = snapshot_path(pair)
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        return sum(1 for _ in f)


def main():
    exchange_name = load_exchange_name()
    tickers = fetch_tickers(exchange_name, WATCH_PAIRS)

    for pair in WATCH_PAIRS:
        data = tickers.get(pair, {"error": "no data"})
        path = append_snapshot(pair, data)
        days = count_days(pair)

        if "error" in data:
            print(f"[watchlist] {pair}: 取得失敗 ({data['error']}) -> {path}")
            continue

        print(f"[watchlist] {pair}: last={data.get('last')} "
              f"24hVolume={data.get('quoteVolume')} "
              f"24h%={data.get('percentage')} "
              f"observed_days={days}/{WATCH_MIN_DAYS_FOR_TRADE} -> {path}")

        if days == WATCH_MIN_DAYS_FOR_TRADE:
            print(f"[watchlist] {pair}: 観測{WATCH_MIN_DAYS_FOR_TRADE}日分に到達。"
                  f"pair_whitelistへの追加を検討する目安に達しました(自動追加はしません)。")


if __name__ == "__main__":
    sys.exit(main())
