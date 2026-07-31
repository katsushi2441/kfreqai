"""kcbrain(crypto, :18328) / kfxbrain(FX, :18326) への薄いクライアント。

kfreqaiがkcbrainを「毎時マーケット判定→エントリーveto」に使うのと同じ用途。
kfreqaihlでは crypto→kcbrain / FX→kfxbrain を、下記の合意した振り分けで呼ぶ:
  - admin(xb_bittensor)  : 無料ローカルgemma4  … providerヘッダを付けない(=各brainの既定)
  - 一般ユーザー/DL利用者 : x402課金レール=DeepSeek … X-*-Provider: deepseek

判定LLMは低速(gemmaで数十秒〜)なので、取引ループ内で同期に1トレードずつ呼ばない。
毎時サイクルの頭で「銘柄一覧をまとめて1回」判定し(opportunity-ranking)、その結果を
エントリーの可否ゲートに使う(kfreqaiのkcbrainゲートと同じfail-open設計)。

トークンは各リポジトリの .env から読む(このリポジトリには置かない・ログにも出さない)。
brainはローカル(127.0.0.1)で動いており、同一ホストからの直叩き。
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

# FreqAI(kfreqaiの非公開モデル)の予測を judgment API 経由で参照する。モデル・特徴量は
# 非公開のまま、予測"結果"だけをロング判断のゲートに使う(kfreqaiと同じ賢さをkfreqaihlへ)。
_JUDGMENT_API = os.environ.get("KFREQAIHL_JUDGMENT_API", "http://127.0.0.1:18321")


def freqai_long_ok(coin, timeout=5):
    """FreqAI(非公開モデル)の判定でロング可否を返す。閾値判定(long_ok)は非公開側が済ませて
    いるので、ここは結果を使うだけ。予測が無い/障害時は fail-open(True)=従来通り。"""
    try:
        pair = "%s/USDT" % coin
        url = _JUDGMENT_API + "/v1/freqai/predict?pair=" + urllib.parse.quote(pair)
        with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        if not d.get("available"):
            return True
        return bool((d.get("prediction") or {}).get("long_ok"))
    except Exception:
        return True

# market -> 接続情報。tokenは各 .env から読む(環境変数優先)。
# kcbrainとkfxbrainは入力エンベロープが違う:
#   crypto(kcbrain): {"assets":[{"symbol":"BTC_USDT",...}]}
#   fx(kfxbrain)   : {"pairs":[{"pair":"EUR_USD",...}]}
# どちらもシンボルは BASE_QUOTE 形式必須。返り値は共通で BrainResponse.result。
_BRAINS = {
    "crypto": {
        "url": os.environ.get("KCBRAIN_URL", "http://127.0.0.1:18328"),
        "env_path": "/home/kojima/work/kcbrain/.env",
        "token_env": "KCBRAIN_API_TOKEN",
        "token_header": "X-KCBRAIN-Token",
        "provider_header": "X-KCBRAIN-Provider",
        "asset_field": "assets", "id_field": "symbol", "quote": "USDT",
    },
    "fx": {
        "url": os.environ.get("KFXBRAIN_URL", "http://127.0.0.1:18326"),
        "env_path": "/home/kojima/work/kfxbrain/.env",
        "token_env": "KFXBRAIN_API_TOKEN",
        "token_header": "X-KFXBrain-Token",
        "provider_header": "X-KFXBrain-Provider",
        "asset_field": "pairs", "id_field": "pair", "quote": "USD",
    },
}

# エントリー可否ゲートの判断基準(kfreqaiのkcbrainゲートと同じ思想)。
_AVOID_DIRECTIONS = {"avoid"}          # 両建てとも見送り
_VETO_SEVERITIES = {"high", "critical"}  # 異常検知の強度


def provider_for(username, admin_username="xb_bittensor"):
    """ユーザー種別 -> brainのprovider。adminのみ無料gemma、他はx402 DeepSeek。"""
    return "gemma" if username == admin_username else "deepseek"


# Bankr x402課金レール(一般テナントの判断はここを通り、テナントのagentウォレットが払う)
_BANKR_BASE = os.environ.get(
    "KURAGE_HL_BANKR_BASE",
    "https://x402.bankr.bot/0x444fadbd6e1fed0cfbf7613b6c9f91b9021eecbd")
_BANKR_SERVICE = {"crypto": "kcbrain", "fx": "fxbrain"}
_BANKR_PATHS = {
    "/v1/market/opportunity-ranking": "/market/opportunity-ranking",
    "/v1/market/anomaly": "/market/anomaly",
}


def _post_x402(market, path, payload, agent_key, timeout=300):
    """Bankr経由でbrainを呼び、テナントagentウォレットからx402自動支払い。"""
    import x402_pay
    url = "%s/%s%s" % (_BANKR_BASE, _BANKR_SERVICE[market], _BANKR_PATHS[path])
    status, data = x402_pay.pay_and_call(url, payload, agent_key, timeout)
    if status == 402:
        raise RuntimeError("x402 payment rejected (insufficient USDC?): %s" % str(data)[:120])
    if status != 200:
        raise RuntimeError("bankr %s: %s" % (status, str(data)[:120]))
    # Bankr CLI/handler経由のレスポンスは {response: {...}} で包まれる場合がある
    return data.get("response") if isinstance(data.get("response"), dict) else data


def _load_token(market):
    b = _BRAINS[market]
    tok = os.environ.get(b["token_env"])
    if tok:
        return tok
    with open(b["env_path"], encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith(b["token_env"] + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("%s not found in env or %s" % (b["token_env"], b["env_path"]))


def _headers(market, provider):
    b = _BRAINS[market]
    h = {"Content-Type": "application/json", b["token_header"]: _load_token(market)}
    # provider=deepseek(x402課金レール)のときだけヘッダを付ける。gemmaは無指定=各brainの既定。
    if provider == "deepseek":
        h[b["provider_header"]] = "deepseek"
    return h


def _post(market, path, payload, provider, timeout=300, retries=1):
    """brainにPOST。gemma4は低頻度でJSONが崩れて502を返すため、502に限りリトライ。"""
    b = _BRAINS[market]
    last_err = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            b["url"] + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=_headers(market, provider), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code != 502 or attempt >= retries:
                raise
    raise last_err


def _base(coin):
    """内部コイン名 -> 素のベースシンボル。builder-dex接頭辞(xyz:)と、1000x接頭辞の
    先頭小文字k(kPEPE/kSHIB)を落として大文字化。ゲート辞書のキーに使う。"""
    s = coin.split(":", 1)[-1]  # "xyz:EUR" -> "EUR"
    if len(s) >= 2 and s[0] == "k" and s[1].isupper():  # kPEPE -> PEPE
        s = s[1:]
    return s.upper()


def _brain_id(coin, market):
    """brainに渡す BASE_QUOTE 形式(crypto=..._USDT / fx=..._USD)。"""
    return _base(coin) + "_" + _BRAINS[market]["quote"]


def build_asset_evidence(coin, df, market):
    """指標付きDataFrame(strategy_core.populate_indicators済み)から、brainに渡す
    証拠1件を作る。id_fieldはmarketで symbol(crypto)/pair(fx) を切替。"""
    last = df.iloc[-1]
    close = float(last["close"])
    prev24 = float(df["close"].iloc[-25]) if len(df) >= 25 else float(df["close"].iloc[0])
    chg24 = (close - prev24) / prev24 * 100.0 if prev24 else 0.0

    def _num(key, nd):
        v = last.get(key)
        return round(float(v), nd) if v == v and v is not None else None  # NaN除外

    tech = {"price": round(close, 6), "change_24h_pct": round(chg24, 2),
            "rsi": _num("rsi", 1), "ema_fast": _num("ema_fast", 6),
            "ema_slow": _num("ema_slow", 6), "atr_pct": _num("atr_pct", 3)}
    tech = {k: v for k, v in tech.items() if v is not None}
    return {_BRAINS[market]["id_field"]: _brain_id(coin, market),
            "technicals": tech,
            "market": {"last_price": round(close, 6), "volume": round(float(last["volume"]), 4)}}


def market_gate(market, assets, provider, timeframe="H1", timeout=300, agent_key=None):
    """opportunity-ranking + anomaly を1回ずつ呼び、銘柄ごとの可否ゲートを作る。
    返り値: {SYMBOL: {"direction": "long|short|watch|avoid", "veto": bool, "why": str}}。
    assets = build_asset_evidence の配列(最大40件)。失敗時は例外(呼び出し側でfail-open)。

    provider=deepseek(一般テナント)は必ずBankr x402経由で、そのテナントの
    agentウォレット(agent_key)から自動支払いする。無料の直叩きはさせない。"""
    assets = assets[:40]
    payload = {"timeframe": timeframe, _BRAINS[market]["asset_field"]: assets}
    if provider == "deepseek":
        if not agent_key:
            raise RuntimeError("x402 payment wallet required (agent_key missing)")
        _call = lambda path: _post_x402(market, path, payload, agent_key, timeout)
    else:
        _call = lambda path: _post(market, path, payload, provider, timeout)
    rank = _call("/v1/market/opportunity-ranking")
    result = (rank or {}).get("result") or {}
    gate = {}
    for r in (result.get("ranking") or []):
        if not isinstance(r, dict):
            continue  # LLM出力ゆれ(文字列だけの行など)は無視
        sym = str(r.get("symbol") or r.get("pair") or "").upper().split("_")[0]
        if not sym:
            continue
        direction = str(r.get("direction", "watch")).lower()
        gate[sym] = {"direction": direction,
                     "veto": direction in _AVOID_DIRECTIONS,
                     "score": r.get("score"), "confidence": r.get("confidence"),
                     "why": (r.get("drivers") or [""])[0]}
    # 異常検知は「high/critical かつ強気でない」を追加のvetoとして重ねる(kfreqai同様)
    try:
        anom = _call("/v1/market/anomaly")
        for a in ((anom.get("result") or {}).get("anomalies") or []):
            if not isinstance(a, dict):
                continue
            sym = str(a.get("symbol") or a.get("pair") or "").upper().split("_")[0]
            if not sym:
                continue
            sev = str(a.get("severity", "")).lower()
            adir = str(a.get("direction", "")).lower()
            if sev in _VETO_SEVERITIES and adir != "bullish":
                g = gate.setdefault(sym, {"direction": "watch", "veto": False})
                g["veto"] = True
                g["why"] = "anomaly:%s/%s" % (a.get("type"), sev)
    except Exception:
        pass  # 異常検知が落ちてもopportunityゲートは活かす
    return gate


def build_tenant_gates(market, assets, tenants, admin_username="xb_bittensor"):
    """テナント別の判断ゲートを作る(username -> gate)。
    admin=無料ローカル(1回だけ計算して共有)。一般テナント=各自のagentウォレットで
    Bankr x402自動支払い(1テナント=1判断=1支払い。タダ乗り・共有なし)。
    支払い失敗/残高不足はそのテナントだけ空ゲート(fail-open)。"""
    gates = {}
    admin_gate = None
    for t in tenants:
        username = t["username"]
        try:
            if provider_for(username, admin_username) == "gemma":
                if admin_gate is None:
                    admin_gate = market_gate(market, assets, provider="gemma")
                gates[username] = admin_gate
            else:
                gates[username] = market_gate(
                    market, assets, provider="deepseek",
                    agent_key=t.get("agent_private_key"))
        except Exception as exc:
            gates[username] = {}
            print("[brain] tenant %s x402 gate failed (%s): %s"
                  % (username, market, str(exc)[:120]), flush=True)
    return gates


def entry_allowed(gate, coin, side):
    """ゲートに照らしてこのエントリー(coin, side=long/short)を許すか。
    kfreqai同様fail-open: ゲートに銘柄が無ければ許可(None扱い)。
    - veto(avoid/異常) → 両建てとも不可
    - direction=long → shortを不可 / direction=short → longを不可 / watch → 両方可"""
    g = gate.get(_base(coin))
    if not g:
        return True, "no-gate(fail-open)"
    if g.get("veto"):
        return False, "veto:" + str(g.get("why", g.get("direction")))
    d = g.get("direction", "watch")
    if d == "long" and side == "short":
        return False, "brain=long"
    if d == "short" and side == "long":
        return False, "brain=short"
    return True, "brain=%s" % d
