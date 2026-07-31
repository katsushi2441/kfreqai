"""Bankr x402 自動支払いクライアント。

テナントのagentウォレット(秘密鍵はtenant_store、Hyperliquid注文署名と同じ鍵)で
EIP-3009 TransferWithAuthorization(Base USDC)をサーバー側で署名し、
Bankrのx402エンドポイントへ自動で支払う。ユーザー操作は不要——
agentウォレットにBase USDCを入れておくだけで毎時のAI判断が自動決済される。

支払いはX-PAYMENTヘッダの再POSTで行う(x402標準)。402以外が返った場合は
そのままステータスとボディを返し、呼び出し側がfail-openを判断する。
"""
import base64
import json
import secrets
import time
import urllib.error
import urllib.request

from eth_account import Account
from eth_account.messages import encode_typed_data

_EIP712_DOMAIN = [
    {"name": "name", "type": "string"},
    {"name": "version", "type": "string"},
    {"name": "chainId", "type": "uint256"},
    {"name": "verifyingContract", "type": "address"},
]
_TRANSFER_TYPES = [
    {"name": "from", "type": "address"},
    {"name": "to", "type": "address"},
    {"name": "value", "type": "uint256"},
    {"name": "validAfter", "type": "uint256"},
    {"name": "validBefore", "type": "uint256"},
    {"name": "nonce", "type": "bytes32"},
]


def _post_json(url, payload, headers, timeout):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(body)
        except Exception:
            data = {"raw": body[:300]}
        return exc.code, data


def _sign_payment(challenge, private_key):
    accepts = challenge.get("accepts") or []
    acc = next((a for a in accepts if a.get("scheme") == "exact"), None)
    if not acc:
        raise RuntimeError("no 'exact' scheme in x402 challenge")
    account = Account.from_key(private_key)
    authorization = {
        "from": account.address,
        "to": acc["payTo"],
        "value": str(acc["maxAmountRequired"]),
        "validAfter": "0",
        "validBefore": str(int(time.time()) + int(acc.get("maxTimeoutSeconds") or 600)),
        "nonce": "0x" + secrets.token_hex(32),
    }
    extra = acc.get("extra") or {}
    full_message = {
        "types": {"EIP712Domain": _EIP712_DOMAIN,
                  "TransferWithAuthorization": _TRANSFER_TYPES},
        "domain": {"name": extra.get("name", "USD Coin"),
                   "version": extra.get("version", "2"),
                   "chainId": 8453,
                   "verifyingContract": acc["asset"]},
        "primaryType": "TransferWithAuthorization",
        "message": authorization,
    }
    signed = Account.sign_message(encode_typed_data(full_message=full_message), private_key)
    signature = signed.signature.hex()
    if not signature.startswith("0x"):
        signature = "0x" + signature
    payment = {
        "x402Version": challenge.get("x402Version", 1),
        "scheme": "exact",
        "network": acc.get("network"),
        "payload": {"signature": signature, "authorization": authorization},
    }
    return base64.b64encode(
        json.dumps(payment, separators=(",", ":")).encode("utf-8")).decode("ascii")


def pay_and_call(url, payload, private_key, timeout=300):
    """x402エンドポイントをPOST。402なら自動署名・自動支払いして再POST。
    返り値: (status, data)。支払い後も402なら残高不足等(呼び出し側でfail-open)。"""
    status, data = _post_json(url, payload, {}, min(timeout, 60))
    if status != 402:
        return status, data
    x_payment = _sign_payment(data, private_key)
    return _post_json(url, payload, {"X-PAYMENT": x_payment}, timeout)
