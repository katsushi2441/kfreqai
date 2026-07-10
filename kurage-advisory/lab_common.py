"""kfreqai研究ラボ(反省会/研究員/ニュースシグナル)の共通ヘルパー。

- gemma4は思考型モデルなので "think": False 必須(/home/kojima/work/CLAUDE.md参照)
- ccxtはホストに無いためkfreqaiコンテナ内でdocker exec実行(hourly_regime.py踏襲)
- 成果物はuser_data/lab/配下のJSONL(gitignore圏内、実データはコミットしない)
"""
import json
import os
import subprocess

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAB_DIR = os.path.join(BASE_DIR, "user_data", "lab")
CONTAINER_NAME = "kfreqai"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://192.168.0.14:11434")
OLLAMA_MODEL = os.environ.get("KFREQAI_OLLAMA_MODEL", "gemma4:12b-it-qat")


def ensure_lab_dir():
    os.makedirs(LAB_DIR, exist_ok=True)
    return LAB_DIR


def jsonl_append(path, record):
    ensure_lab_dir()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def jsonl_load(path, limit=None):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows[-limit:] if limit else rows


def ollama_generate(prompt, num_predict=800, temperature=0.2, timeout=180):
    payload = {
        "model": OLLAMA_MODEL,
        "think": False,  # gemma4はthink必須OFF(隠れ推論がnum_predictを食い潰し空応答になる)
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    resp = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("response") or ""


def extract_json(text):
    """LLM出力からJSONオブジェクト/配列を抜き出してパースする(コードフェンス等を許容)。"""
    text = text.strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    return None


def fetch_ohlcv_via_container(pair, timeframe, since_ms, limit):
    """kfreqaiコンテナ内のccxtでOHLCVを取得(ホストにccxt無し)。"""
    script = (
        "import ccxt, json\n"
        "ex = ccxt.mexc({'enableRateLimit': True, 'timeout': 30000})\n"
        f"print(json.dumps(ex.fetch_ohlcv({pair!r}, {timeframe!r}, since={since_ms}, limit={limit})))\n"
    )
    result = subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "python3", "-c", script],
        capture_output=True, text=True, timeout=90,
    )
    if result.returncode != 0:
        raise RuntimeError("ccxt fetch failed: %s" % (result.stderr or result.stdout)[-300:])
    return json.loads(result.stdout)
