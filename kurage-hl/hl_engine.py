"""kfreqaihl 常時稼働エンジン — 方式B(1プロセスで全テナント)。50人規模を想定。

kfreqaiのfreqtrade `trade` ループに相当する部分を、マルチテナント用に自作した
もの。freqtradeは1プロセス1口座なので流用できないが、戦略の頭脳(strategy_core)と
バックテストは流用済み。ここが唯一の「自作するライブループ」。

50人×10銘柄でも破綻しない設計:
  - ローソク足は【銘柄ごとに1回だけ】取得してキャッシュし全テナントで使い回す
    (50人×10銘柄=500回ではなく10回取得)。ここがスケールの肝。
  - 指標計算は各テナントのパラメータで行う(EMA期間/枠数が人により違う)。
    500行のpandasなのでCPU的に軽い(ミリ秒)。
  - サイクルはローソク足確定間隔(1hなら1時間に1回)。秒単位で叩かない。

1サイクルの各テナント処理:
  1) 保有ポジションの決済判断(ストップ / ピークトレール / 決済シグナル)
     = kfreqaiのcustom_stoploss/custom_exit/populate_exit_trendのミラー
  2) 空き枠を、ユニバースのエントリーシグナルで埋める(max_open_trades運用のミラー)

発注/決済はhl_connector経由でモック/DRY-RUNガードつき。HL_MOCK=1なら
シミュレーション約定で、実注文は飛ばない。
"""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "user_data", "strategies"))
import strategy_core  # noqa: E402  (shared edge)

import hl_brain_client as brain  # noqa: E402  (kcbrain/kfxbrain 判断ゲート)
import hl_connector  # noqa: E402
import hl_loop  # noqa: E402  (fetch_candles, _core_params, DEFAULT_UNIVERSE)
import hl_presets  # noqa: E402
import hl_schemas  # noqa: E402
import tenant_store  # noqa: E402

INTERVAL = os.environ.get("HL_DEFAULT_INTERVAL", "1h")
CYCLE_SECONDS = int(os.environ.get("HL_CYCLE_SECONDS", "3600"))  # 1h既定
ADMIN_USERNAME = os.environ.get("HL_ADMIN_USERNAME", "xb_bittensor")
# kcbrain/kfxbrain判断ゲート。既定ON。fail-open(brainが落ちても取引は止めない)。
BRAIN_GATE_ENABLED = os.environ.get("HL_BRAIN_GATE", "1") == "1"
# エントリーシグナルの出どころ(2026-08-03追加):
#   core   = strategy_core のEMAクロス(従来の既定)
#   freqai = kfreqai本番のFreqAI予測(judgment API経由の long_ok)をロングの根拠にする
#   both   = どちらかが出たら入る(ロングはfreqai優先、ショートはcoreのみ)
# FreqAI予測は crypto(BASE/USDT)にしか無いので FX(xyz:*) は常に core。
ENTRY_SOURCE = os.environ.get("HL_ENTRY_SOURCE", "core").strip().lower()
_GATE_SHADOW_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "user_data", "hl_brain_gate.json")
_SCHEMA_DEFAULTS = {s["key"]: s["default"]
                    for s in hl_schemas.SCHEMAS[hl_schemas.DEFAULT_STRATEGY]}


def fetch_candle_cache(universe, interval=INTERVAL):
    """銘柄ごとに生ローソク足を1回だけ取得(全テナントで使い回す)。{coin: raw_df}。"""
    cache = {}
    for coin in universe:
        try:
            df = hl_loop.fetch_candles(coin, interval)
            if not df.empty:
                cache[coin] = df
        except Exception as exc:
            print("[engine] candle fetch failed %s: %s" % (coin, str(exc)[:100]), flush=True)
    return cache


def _profit_ratio(entry_px, cur_px, is_short):
    """価格ベースの損益率(kfreqaiのcurrent_profitと同じ意味・レバ非考慮)。"""
    if not entry_px:
        return 0.0
    r = (cur_px - entry_px) / entry_px
    return -r if is_short else r


def manage_position(username, pos, df, p):
    """保有ポジション1件の決済判断。exit理由(文字列)かNone。
    kfreqaiの custom_stoploss / custom_exit / populate_exit_trend のミラー。"""
    coin = pos["coin"]
    is_short = pos["is_short"]
    cur_px = float(df["close"].iloc[-1])
    profit = _profit_ratio(pos["entry_px"], cur_px, is_short)

    # (a) ストップロス(custom_stoploss相当・価格変化率)
    if profit <= float(p.get("stoploss_pct", -6.0)) / 100.0:
        return "stop_loss"

    # (b) ピークPnLトレール(custom_exit相当)
    peak = tenant_store.update_peak(username, coin, profit)
    trigger = float(p.get("peak_trail_trigger_pct", 4.0)) / 100.0
    giveback = float(p.get("peak_trail_giveback_pct", 25.0)) / 100.0
    if peak >= trigger and peak > 0 and (peak - profit) / peak >= giveback:
        return "peak_trail"

    # (c) 決済シグナル(populate_exit_trend相当)
    exit_cond = (strategy_core.exit_short_cond(df, p) if is_short
                 else strategy_core.exit_long_cond(df, p))
    if bool(exit_cond.iloc[-1]):
        return "exit_signal"
    return None


def build_brain_gates(cache, tenants, market="crypto"):
    """このサイクルの判断ゲートを provider ごとに1回だけ作る(kfreqaiのkcbrain毎時判定と
    同じ発想)。crypto→kcbrain / FX→kfxbrain。admin=無料gemma / 一般=x402 DeepSeek。
    取引ループ内で1トレードずつLLMを呼ばず、ここでまとめて判定してvetoゲートにする。
    brainが落ちても {} を返し fail-open(取引は止めない)。"""
    if not BRAIN_GATE_ENABLED or not tenants:
        return {}
    # 判断用の証拠は共通(=市場観)なので既定パラメータの指標で1回だけ作る
    assets = []
    for coin, df in cache.items():
        try:
            d = strategy_core.populate_indicators(df.copy(), _SCHEMA_DEFAULTS)
            assets.append(brain.build_asset_evidence(coin, d, market))
        except Exception:
            continue
    # テナント別ゲート: admin=無料ローカル共有 / 一般=各自のagentウォレットでx402自動支払い
    gates = brain.build_tenant_gates(market, assets, tenants, ADMIN_USERNAME)
    # 可視化/デバッグ用にシャドー保存(取引判断はメモリのgatesを使う)
    try:
        import json
        with open(_GATE_SHADOW_PATH, "w", encoding="utf-8") as f:
            json.dump({"ts": int(time.time()), "market": market, "gates": gates},
                      f, ensure_ascii=False)
    except Exception:
        pass
    return gates


def decide_entry(coin, df, p, source=None):
    """このcoinに新規で入るかを決める。HL_ENTRY_SOURCE で根拠を切り替える。

    core   : strategy_core のEMAクロス(従来)。クロスした瞬間の足だけが対象なので
             シグナルは稀(実測で25銘柄中0〜1件)。
    freqai : kfreqai本番のFreqAI予測(long_ok)をロングの根拠にする。本番と同じ
             エントリー条件を非公開側で判定した結果なので、エッジがそのまま乗る。
             ショートの予測は出力していないため、ショートは core に委ねる。
    both   : freqaiでロングが出ればそれを採用し、出なければ core にフォールバック。

    予測は crypto(BASE/USDT)にしか無い。FX(xyz:*)は常に core を使う。
    """
    src = (source or ENTRY_SOURCE)
    allow_long = bool(p.get("is_long_enabled", True))
    allow_short = bool(p.get("is_short_enabled", False))
    is_dex = ":" in coin  # xyz:EUR などのbuilder-dex銘柄には予測が無い

    if src in ("freqai", "both") and allow_long and not is_dex:
        sig = brain.freqai_long_signal(coin)
        if sig:
            return sig
        if src == "freqai":
            # freqai単独指定のときはショートだけcoreに委ねる(ロングはfreqaiが唯一の根拠)
            if not allow_short:
                return {"side": None, "reason": "freqai:long_okでない"}
            d = strategy_core.decide_target_side(df, p, allow_long=False, allow_short=True)
            return d

    return strategy_core.decide_target_side(
        df, p, allow_long=allow_long, allow_short=allow_short)


def run_tenant(username, cache, interval=INTERVAL, gates=None):
    """1テナント分: 決済管理 → 空き枠エントリー。結果サマリを返す。
    gates = build_brain_gates の結果(provider別)。このテナントのproviderのゲートで
    エントリーの可否を判定する(kcbrain/kfxbrain判断)。"""
    tenant = tenant_store.get_or_create(username, hl_connector.generate_agent_wallet)
    if not tenant.get("main_wallet_address") or not tenant.get("agent_approved"):
        return {"username": username, "skipped": "not approved"}
    gate = (gates or {}).get(username) or {}
    p = hl_loop._core_params(username)
    slots = max(1, int(p.get("max_open_trades", hl_schemas.DEFAULT_MAX_OPEN_TRADES)))
    dash = hl_connector.get_dashboard(tenant["main_wallet_address"])
    positions = dash.get("positions") or []
    held = {pos["coin"] for pos in positions}
    closed, opened = [], []

    # 1) 決済管理
    for pos in positions:
        coin = pos["coin"]
        df = cache.get(coin)
        if df is None:
            continue
        df = strategy_core.populate_indicators(df.copy(), p)
        reason = manage_position(username, pos, df, p)
        if reason:
            try:
                hl_connector.close_position(tenant["agent_private_key"],
                                            tenant["main_wallet_address"],
                                            coin, pos["size"], pos["is_short"])
                tenant_store.clear_peak(username, coin)
                held.discard(coin)
                closed.append({"coin": coin, "reason": reason})
            except NotImplementedError as exc:
                closed.append({"coin": coin, "reason": reason, "skipped": str(exc)[:60]})

    # 2) 空き枠エントリー
    available = max(0, slots - len(held))
    equity = float(dash.get("account_value_usd") or 0)
    for coin in hl_loop.DEFAULT_UNIVERSE:
        if available <= 0:
            break
        if coin in held:
            continue
        df = cache.get(coin)
        if df is None:
            continue
        df = strategy_core.populate_indicators(df.copy(), p)
        d = decide_entry(coin, df, p)
        if not d.get("side"):
            continue
        # kcbrain/kfxbrain判断ゲート: このprovider(admin=gemma/一般=deepseek)の市場観に
        # 反するエントリーは見送る。ゲートに銘柄が無ければfail-open(許可)。
        ok_gate, why = brain.entry_allowed(gate, coin, d["side"])
        if not ok_gate:
            opened.append({"coin": coin, "side": d["side"], "gated": why})
            continue
        # FreqAI(非公開モデル)の予測ゲート: ロングは下落見込みなら見送る(ショートは
        # 下落局面でむしろ有効なので対象外)。kfreqaiの賢さをモデル非公開のまま効かせる。
        if d["side"] == "long" and not brain.freqai_long_ok(coin):
            opened.append({"coin": coin, "side": "long", "gated": "freqai:予測が上昇でない"})
            continue
        price = float(df["close"].iloc[-1])
        notional = hl_loop._slot_notional(equity, p)
        # 銘柄ごとの許容小数桁で切り捨てる(一律4桁丸めはATOM等でinvalid sizeになる)。
        # 切り上げ方向は証拠金超過の危険があるので必ずfloor。
        step = 10 ** hl_connector.sz_decimals(coin)
        size = int((notional / price) * step) / step if price else 0
        if size <= 0 or size * price < 10.0:
            # Hyperliquidの最小注文額は$10。黙ってスキップせず理由をログに残す
            print("[engine] skip %s %s: below min order ($%.2f < $10)"
                  % (coin, d["side"], size * price), flush=True)
            continue
        try:
            res = hl_connector.place_order(tenant["agent_private_key"],
                                           tenant["main_wallet_address"],
                                           coin, d["side"] == "long", size,
                                           leverage=int(p.get("leverage", 2)))
            filled, detail = hl_connector.order_fill_info(res)
            if filled:
                tenant_store.update_peak(username, coin, 0.0)  # 建玉時にピーク初期化
                held.add(coin)
                opened.append({"coin": coin, "side": d["side"], "size": size})
                available -= 1
            else:
                # 発注が受理されなかった: 誤って成功扱いにしない(以前のバグ)
                opened.append({"coin": coin, "side": d["side"], "failed": str(detail)[:120]})
                print("[engine] order rejected %s %s: %s" % (coin, d["side"], detail), flush=True)
        except NotImplementedError as exc:
            opened.append({"coin": coin, "side": d["side"], "skipped": str(exc)[:60]})

    return {"username": username, "slots": slots, "open_before": len(positions),
            "closed": closed, "opened": opened}


def run_cycle(interval=INTERVAL):
    """全アクティブテナントを1サイクル処理。ローソク足は銘柄ごと1回だけ取得。"""
    tenants = tenant_store.list_active_tenants()
    if not tenants:
        return {"tenants": 0, "results": []}
    cache = fetch_candle_cache(hl_loop.DEFAULT_UNIVERSE, interval)
    # アクティブなテナントに存在するproviderの分だけ判断ゲートを作る
    # (adminがいればgemmaを、一般ユーザーがいればdeepseek/x402を1回ずつ)。
    gates = build_brain_gates(cache, tenants, market="crypto")
    results = []
    for t in tenants:
        try:
            results.append(run_tenant(t["username"], cache, interval, gates=gates))
        except Exception as exc:
            results.append({"username": t["username"], "error": str(exc)[:150]})
            print("[engine] tenant %s failed: %s" % (t["username"], traceback.format_exc()[:400]),
                  flush=True)
    return {"tenants": len(tenants), "cached_coins": len(cache), "results": results}


def main():
    import autoreload
    autoreload.start()  # ソース変更で自動再起動(手動restart不要)
    print("[engine] start (interval=%s cycle=%ds mock=%s live=%s)" % (
        INTERVAL, CYCLE_SECONDS, hl_connector.MOCK, hl_connector.LIVE_TRADING), flush=True)
    while True:
        t0 = time.time()
        try:
            out = run_cycle(INTERVAL)
            print("[engine] cycle: tenants=%s coins=%s" % (
                out.get("tenants"), out.get("cached_coins")), flush=True)
        except Exception:
            print("[engine] cycle failed: %s" % traceback.format_exc()[:400], flush=True)
        # ペーパーFX(実弾なし・テナント別の仮想売買)も同じ毎時サイクルで回す
        try:
            import hl_paper_fx
            pout = hl_paper_fx.run_cycle()
            print("[engine] paperfx: tenants=%s coins=%s" % (
                pout.get("paper_fx_tenants"), pout.get("fx_coins")), flush=True)
        except Exception:
            print("[engine] paperfx failed: %s" % traceback.format_exc()[:400], flush=True)
        # ペーパー現物(実弾なし・ロングのみ・レバ1倍・清算なし)。kfreqaiとの比較・
        # 現物ロング志向ユーザー向け(2026-07-28)
        try:
            import hl_paper_spot
            sout = hl_paper_spot.run_cycle()
            print("[engine] paperspot: tenants=%s coins=%s" % (
                sout.get("paper_spot_tenants"), sout.get("spot_coins")), flush=True)
        except Exception:
            print("[engine] paperspot failed: %s" % traceback.format_exc()[:400], flush=True)
        # 2026-07-27: ペーパーMEXC先物は撤去。MEXC先物APIが使えないという誤情報を前提に
        # ここへ自作エンジンを同居させていたが、実際はfreqtradeの先物モードで動くため
        # kfreqai側(kfreqai-futures-short: freqtrade dry-run)へ一本化した。
        # これによりkfreqaihl(=Hyperliquid専用)の製品境界も正しく戻る。
        elapsed = time.time() - t0
        time.sleep(max(5, CYCLE_SECONDS - elapsed))


if __name__ == "__main__":
    main()
