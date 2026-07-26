"""MEXC Contract(先物) APIクライアント — kfreqaiのMEXC先物ショート対応の土台。

背景(履歴で合意済みの方式):
- freqtradeはMEXC先物に非対応(list-exchanges実測: mexc=spotのみ)。よって
  先物の発注は本クライアント(独自Contractクライアント)で行う。
- MEXCは過去、先物の発注APIを一般ユーザーに制限していた。2026-07-26の実測では
  公開contract/detailが apiAllowed: True を返しており開放の可能性が高いが、
  確定は「認証付きの極小テスト発注」でしか分からない(scripts/key_test.py)。

現物(api.mexc.com/api/v3)とは別物:
- ベースURL: https://contract.mexc.com
- 署名: HMAC-SHA256(secret, accessKey + timestamp + paramString)
  ヘッダ: ApiKey / Request-Time / Signature / Content-Type: application/json
- side: 1=ロング建て 2=ショート決済 3=ショート建て 4=ロング決済
- openType: 1=isolated 2=cross

安全ガード:
- キーは環境変数(MEXCF_API_KEY / MEXCF_API_SECRET)からのみ。ログに出さない。
- 発注系は MEXCF_LIVE=1 を明示しない限り実行しない(既定は読み取りのみ)。
"""
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = os.environ.get("MEXCF_BASE_URL", "https://contract.mexc.com")
LIVE = os.environ.get("MEXCF_LIVE", "0") == "1"

# side定数(MEXC Contract仕様)
SIDE_OPEN_LONG = 1
SIDE_CLOSE_SHORT = 2
SIDE_OPEN_SHORT = 3
SIDE_CLOSE_LONG = 4
OPEN_TYPE_ISOLATED = 1
OPEN_TYPE_CROSS = 2
ORDER_TYPE_LIMIT = 1
ORDER_TYPE_MARKET = 5


class MexcfError(RuntimeError):
    pass


def _keys():
    k = os.environ.get("MEXCF_API_KEY", "").strip()
    s = os.environ.get("MEXCF_API_SECRET", "").strip()
    if not k or not s:
        raise MexcfError("MEXCF_API_KEY / MEXCF_API_SECRET が未設定です(kurage-mexcf/.env)")
    return k, s


def _sign(access_key, secret, ts_ms, param_str):
    payload = access_key + ts_ms + param_str
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _request(method, path, params=None, auth=False, timeout=20):
    """MEXC Contract APIへのリクエスト。GET/DELETEはクエリ文字列、POSTはJSON本文が
    署名対象(paramString)。"""
    params = params or {}
    url = BASE_URL + path
    body = None
    headers = {"Content-Type": "application/json"}
    if method in ("GET", "DELETE"):
        # 署名対象は辞書順のクエリ文字列
        qs = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}"
                      for k, v in sorted(params.items())) if params else ""
        if qs:
            url += "?" + qs
        param_str = qs
    else:  # POST
        param_str = json.dumps(params, separators=(",", ":")) if params else ""
        body = param_str.encode()
    if auth:
        ak, sk = _keys()
        ts = str(int(time.time() * 1000))
        headers.update({"ApiKey": ak, "Request-Time": ts,
                        "Signature": _sign(ak, sk, ts, param_str)})
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise MexcfError(f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:300]}") from exc
    if isinstance(out, dict) and out.get("success") is False:
        raise MexcfError(f"MEXC error code={out.get('code')} message={str(out.get('message'))[:200]}")
    return out


# ---------------------------------------------------------------- 公開(認証不要)
def contract_detail(symbol=None):
    p = {"symbol": symbol} if symbol else {}
    return _request("GET", "/api/v1/contract/detail", p).get("data")


def ticker(symbol):
    return _request("GET", "/api/v1/contract/ticker", {"symbol": symbol}).get("data")


def kline(symbol, interval="Min60", start=None, end=None):
    """ローソク足。intervalはMEXC仕様: Min1/Min5/Min15/Min30/Min60/Hour4/Hour8/Day1/Week1/Month1"""
    p = {"interval": interval}
    if start:
        p["start"] = int(start)
    if end:
        p["end"] = int(end)
    return _request("GET", f"/api/v1/contract/kline/{symbol}", p).get("data")


# ---------------------------------------------------------------- 認証(読み取り)
def account_assets():
    """先物口座の資産一覧。キーの有効性・先物権限の確認に使う。"""
    return _request("GET", "/api/v1/private/account/assets", auth=True).get("data")


def open_positions(symbol=None):
    p = {"symbol": symbol} if symbol else {}
    return _request("GET", "/api/v1/private/position/open_positions", p, auth=True).get("data")


def open_orders(symbol=None):
    p = {"symbol": symbol} if symbol else {}
    return _request("GET", "/api/v1/private/order/list/open_orders" + (f"/{symbol}" if symbol else ""),
                    {}, auth=True).get("data")


# ---------------------------------------------------------------- 認証(発注系)
def submit_order(symbol, side, vol, leverage, price=None,
                 order_type=ORDER_TYPE_MARKET, open_type=OPEN_TYPE_ISOLATED,
                 external_oid=None):
    """発注。side: 1=ロング建て 3=ショート建て 2/4=決済。
    ★これが「MEXCが一般口座の先物API発注を許すか」の最終関門。
    MEXCF_LIVE=1 でない限り実行しない(実弾ガード)。"""
    if not LIVE:
        raise MexcfError("発注はMEXCF_LIVE=1のときのみ(現在は読み取り専用モード)")
    p = {"symbol": symbol, "side": int(side), "vol": float(vol),
         "leverage": int(leverage), "type": int(order_type), "openType": int(open_type)}
    if price is not None:
        p["price"] = float(price)
    if external_oid:
        p["externalOid"] = str(external_oid)[:32]
    return _request("POST", "/api/v1/private/order/submit", p, auth=True)


def cancel_all(symbol=None):
    if not LIVE:
        raise MexcfError("取消もMEXCF_LIVE=1のときのみ")
    p = {"symbol": symbol} if symbol else {}
    return _request("POST", "/api/v1/private/order/cancel_all", p, auth=True)
