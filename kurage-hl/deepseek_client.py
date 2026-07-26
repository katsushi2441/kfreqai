"""一般ユーザー向けチャット(x402課金)用のDeepSeekクライアント。

鍵はkcbrain/.envのKCBRAIN_DEEPSEEK_API_KEYを読み回す(新規にキーを
発行・重複管理しない。このワークスペースの既存キー流用の方針に従う)。
DeepSeekも(gemma4のthink:false同様に)"thinking"を明示的に無効化しないと
無駄な推論トークンでmax_tokensを消費するため、必ず指定する
(kcbrain/src/kcbrain/ollama.pyの_chat_deepseekと同じ作法)。
"""
import os

import requests

KCBRAIN_ENV_PATH = "/home/kojima/work/kcbrain/.env"
BASE_URL = os.environ.get("KCBRAIN_DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("KCBRAIN_DEEPSEEK_MODEL", "deepseek-v4-flash")
TIMEOUT = int(os.environ.get("KCBRAIN_DEEPSEEK_TIMEOUT", "180"))


def _load_key():
    key = os.environ.get("KCBRAIN_DEEPSEEK_API_KEY")
    if key:
        return key
    with open(KCBRAIN_ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("KCBRAIN_DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("KCBRAIN_DEEPSEEK_API_KEY not found in env or %s" % KCBRAIN_ENV_PATH)


def chat(prompt, temperature=0.5, max_tokens=500):
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "thinking": {"type": "disabled"},
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    resp = requests.post(
        f"{BASE_URL}/chat/completions",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {_load_key()}"},
        json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    body = resp.json()
    choices = body.get("choices") or []
    content = str(((choices[0] if choices else {}).get("message") or {}).get("content") or "").strip()
    if not content:
        raise RuntimeError("DeepSeek returned an empty response")
    return content
