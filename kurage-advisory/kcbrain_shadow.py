#!/usr/bin/env python3
"""kcbrainシャドー地合い判定 — ②のシャドージョブ(2026-07-17開始)。

毎時実行。ライブのhourly_regime.py(gemma4自家製プロンプト・価格のみ)に対する
比較対照として、nofx調査で特定した欠落信号(Binance先物のOI変化・funding rate)
を証拠に加えた kcbrain /v1/market/opportunity-ranking と /v1/market/anomaly を
実行し、結果を**シャドー専用ファイルにのみ**書く。

- 書き先: user_data/kcbrain_shadow.json (最新) と
  user_data/lab/kcbrain_shadow_history.jsonl (履歴+その時点のライブadvisory値)
- advisory_state.json には書かない = ライブの取引判断には一切影響しない。
- 2週間ほど蓄積した後、ライブregime判定との不一致点を実現損益と突き合わせて
  ゲート切替を判断する(枠絞り撤回の教訓: 数値的裏付けなしに本番へ入れない)。

銘柄はMEXC出来高上位12に絞る。kcbrainのnum_predict=2200が上限のため、
18銘柄では出力末尾の必須フィールド(market_summary等)が欠けて502になる
ことを実測済み(2026-07-17)。
"""
import datetime
import json
import os
import sys
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "user_data", "strategies"))

import advisory_state
import hourly_regime
import kcbrain_client

SHADOW_PATH = os.path.join(BASE_DIR, "user_data", "kcbrain_shadow.json")
HISTORY_PATH = os.path.join(BASE_DIR, "user_data", "lab", "kcbrain_shadow_history.jsonl")
MAX_ASSETS = 12
BINANCE_FAPI = "https://fapi.binance.com"


def _get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "kfreqai-kcbrain-shadow"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_binance_funding():
    """全先物銘柄のfunding rateを1リクエストで取得。{symbol: rate}"""
    try:
        rows = _get_json(BINANCE_FAPI + "/fapi/v1/premiumIndex")
        return {r["symbol"]: float(r.get("lastFundingRate") or 0) for r in rows}
    except Exception:
        return {}


def fetch_binance_oi_change(symbol):
    """OI履歴(1h×25本)から4h/24h変化率を計算。銘柄が無ければNone。"""
    try:
        rows = _get_json(
            BINANCE_FAPI + "/futures/data/openInterestHist?symbol=%s&period=1h&limit=25" % symbol
        )
        if len(rows) < 5:
            return None
        ois = [float(r["sumOpenInterest"]) for r in rows]
        out = {"oi_change_4h_pct": round((ois[-1] / ois[-5] - 1) * 100, 2)}
        if len(rows) >= 25 and ois[0] > 0:
            out["oi_change_24h_pct"] = round((ois[-1] / ois[0] - 1) * 100, 2)
        return out
    except Exception:
        return None


def build_assets(ohlcv_by_pair):
    """MEXCのOHLCV(1h×30本)から出来高上位MAX_ASSETSの証拠を組む。"""
    import re
    scored = []
    for pair, candles in ohlcv_by_pair.items():
        if len(candles) < 25:
            continue
        # kcbrainのsymbol検証(2〜12文字_2〜12文字)に通らない銘柄(G/USDT等)は除外
        if not re.fullmatch(r"[A-Z0-9]{2,12}_[A-Z0-9]{2,12}", pair.replace("/", "_").upper()):
            continue
        quote_vol_24h = sum(c[5] * c[4] for c in candles[-24:])
        scored.append((quote_vol_24h, pair, candles))
    scored.sort(reverse=True)

    funding = fetch_binance_funding()
    assets = []
    for quote_vol, pair, candles in scored[:MAX_ASSETS]:
        closes = [c[4] for c in candles]
        vols = [c[5] for c in candles]
        price = closes[-1]
        market = {
            "price": price,
            "change_4h_pct": round((closes[-1] / closes[-5] - 1) * 100, 2),
            "change_24h_pct": round((closes[-1] / closes[-25] - 1) * 100, 2),
            "quote_volume_24h_usd": round(quote_vol),
        }
        avg_vol = sum(vols[:-4]) / max(1, len(vols) - 4)
        technicals = {
            "volume_ratio_4h_vs_avg": round(sum(vols[-4:]) / 4 / avg_vol, 2) if avg_vol else None,
            "high_24h": max(c[2] for c in candles[-24:]),
            "low_24h": min(c[3] for c in candles[-24:]),
        }
        derivatives = {}
        bsym = pair.replace("/", "")
        if bsym in funding:
            derivatives["binance_funding_rate"] = funding[bsym]
            oi = fetch_binance_oi_change(bsym)
            if oi:
                derivatives.update(oi)
        asset = {
            "symbol": pair.replace("/", "_"),
            "market": market,
            "technicals": technicals,
        }
        if derivatives:
            asset["derivatives"] = derivatives
        assets.append(asset)
    return assets


def main():
    config = hourly_regime.load_config()
    pairs = config["exchange"]["pair_whitelist"]
    ohlcv = hourly_regime.fetch_ohlcv(config["exchange"]["name"], pairs)
    assets = build_assets(ohlcv)
    if not assets:
        raise RuntimeError("no assets with sufficient OHLCV")

    now_iso = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
    base_req = {"timeframe": "H1", "as_of": now_iso, "assets": assets}

    ranking = kcbrain_client.post(
        "/v1/market/opportunity-ranking",
        {**base_req, "question": "Rank these MEXC spot pairs for long entries over the next 4-24h. "
                                 "Also state the overall market regime (bullish/neutral/bearish). "
                                 "Keep drivers and risks to at most 2 short items per asset."},
    )
    anomaly = kcbrain_client.post(
        "/v1/market/anomaly",
        {**base_req, "question": "Flag conditions that would justify pausing new spot long entries."},
    )

    # ライブadvisoryの現在値を同じ行に記録(後日の突き合わせ用)
    live = advisory_state.read_state()
    live_snapshot = {
        "regime": (live.get("regime") or {}).get("value"),
        "directive": (live.get("directive") or {}).get("value"),
    }

    record = {
        "at": now_iso,
        "n_assets": len(assets),
        "asset_symbols": [a["symbol"] for a in assets],
        "opportunity_ranking": ranking.get("result"),
        "anomaly": anomaly.get("result"),
        "latency_ms": {"ranking": ranking.get("latency_ms"), "anomaly": anomaly.get("latency_ms")},
        "live_advisory": live_snapshot,
        "model": ranking.get("model"),
    }

    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    tmp = SHADOW_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SHADOW_PATH)

    summary = (record["opportunity_ranking"] or {}).get("market_summary", "")
    print("kcbrain shadow ok: %d assets, live=%s, summary=%s" % (
        len(assets), live_snapshot, summary[:120]))


if __name__ == "__main__":
    main()
