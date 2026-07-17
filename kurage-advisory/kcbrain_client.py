"""kcbrain (Kurage Crypto Brain, :18328) への薄いHTTPクライアント。

シャドー評価専用: ここの結果は advisory_state.json には一切書かず、
ライブの取引判断には影響しない。トークンは kcbrain/.env から読む
(このリポジトリには置かない・ログにも出さない)。
"""
import json
import os
import urllib.request

KCBRAIN_URL = os.environ.get("KCBRAIN_URL", "http://127.0.0.1:18328")
KCBRAIN_ENV_PATH = "/home/kojima/work/kcbrain/.env"


def _load_token():
    token = os.environ.get("KCBRAIN_API_TOKEN")
    if token:
        return token
    with open(KCBRAIN_ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("KCBRAIN_API_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("KCBRAIN_API_TOKEN not found in env or %s" % KCBRAIN_ENV_PATH)


def post(path, payload, timeout=900, retries=1):
    """POST payload to kcbrain, return parsed BrainResponse dict. Raises on error.

    gemma4は低頻度でJSONが崩れて502(BrainError)を返すことがあるため、
    502に限り既定1回リトライする。
    """
    last_err = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            KCBRAIN_URL + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-KCBRAIN-Token": _load_token(),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            last_err = RuntimeError("kcbrain %s -> HTTP %s: %s" % (path, exc.code, body))
            if exc.code != 502 or attempt >= retries:
                raise last_err from exc
    raise last_err
