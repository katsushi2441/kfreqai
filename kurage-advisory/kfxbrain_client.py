"""kfxbrain (Kurage FX Brain, :18326) への薄いHTTPクライアント。kcbrain_client.py と対。

社内の定期の仕事（FXデイリーブログ）が、同じサーバーの kfxbrain を直接呼ぶためのもの。
公開している kfxai は外の人向けに Bankr x402 の有料窓口だけを使う作りで、そちらは変えない。
自社の判断を自社で使うのに支払いは要らないので、ここでは x402 を通さない。
トークンは kfxbrain/.env から読む（このリポジトリには置かない・ログにも出さない）。
"""
import json
import os
import urllib.error
import urllib.request

KFXBRAIN_URL = os.environ.get("KFXBRAIN_URL", "http://127.0.0.1:18326")
KFXBRAIN_ENV_PATH = "/home/kojima/work/kfxbrain/.env"


def _load_token():
    token = os.environ.get("KFXBRAIN_API_TOKEN")
    if token:
        return token
    with open(KFXBRAIN_ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("KFXBRAIN_API_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("KFXBRAIN_API_TOKEN not found in env or %s" % KFXBRAIN_ENV_PATH)


def post(path, payload, timeout=900, retries=1):
    """POST payload to kfxbrain, return parsed BrainResponse dict. 502（JSON崩れ）だけ1回やり直す。"""
    last_err = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            KFXBRAIN_URL + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-KFXBRAIN-Token": _load_token()},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            last_err = RuntimeError("kfxbrain %s -> HTTP %s: %s" % (path, exc.code, body))
            if exc.code != 502 or attempt >= retries:
                raise last_err from exc
    raise last_err
