"""Public, judgment-agnostic LLM-calling mechanics shared by judgment_logic.py
(private) and anyone implementing their own local_llm backend.

Pure "how do I call this LLM binary/API" -- no prompts, no thresholds, no
judgment content. Safe to keep public; this is plumbing, not the product.

3-tier fallback (claude -> codex -> gemma4) mirrors what daily_directive.py
originally had inline. gemma4 runs via Ollama and is the only tier that
needs no paid account -- see /home/kojima/work/CLAUDE.md for why "think"
must be False.
"""
import glob
import os
import shutil
import subprocess

import requests

CLAUDE_MODEL = os.environ.get("KFREQAI_CLAUDE_MODEL", "sonnet")
CLAUDE_TIMEOUT = int(os.environ.get("KFREQAI_CLAUDE_TIMEOUT", "180"))
CODEX_MODEL = os.environ.get("KFREQAI_CODEX_MODEL", "gpt-5.6-sol")
CODEX_TIMEOUT = int(os.environ.get("KFREQAI_CODEX_TIMEOUT", "180"))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://192.168.0.14:11434")
OLLAMA_MODEL = os.environ.get("KFREQAI_OLLAMA_MODEL", "gemma4:12b-it-qat")


def resolve_claude_bin():
    found = shutil.which("claude")
    if found:
        return found
    candidates = sorted(glob.glob(
        os.path.expanduser(
            "~/.vscode-server/extensions/anthropic.claude-code-*-linux-x64/resources/native-binary/claude"
        )
    ), reverse=True)
    for path in candidates:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
    return ""


def resolve_codex_bin():
    found = shutil.which("codex")
    if found:
        return found
    candidates = sorted(glob.glob(
        os.path.expanduser(
            "~/.vscode-server/extensions/openai.chatgpt-*-linux-x64/bin/linux-x86_64/codex"
        )
    ), reverse=True)
    for path in candidates:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
    return ""


def call_claude(prompt, model=None, timeout=None):
    claude_bin = resolve_claude_bin()
    if not claude_bin:
        raise RuntimeError("claude binary not found")
    cmd = [
        claude_bin, "-p", "--output-format", "text", "--tools", "",
        "--model", model or CLAUDE_MODEL, prompt,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout or CLAUDE_TIMEOUT)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"claude cli returncode={result.returncode} detail={detail[:300]!r}")
    return result.stdout.strip()


def call_codex(prompt, model=None, timeout=None, cwd=None):
    codex_bin = resolve_codex_bin()
    if not codex_bin:
        raise RuntimeError("codex binary not found")
    cmd = [
        codex_bin, "exec", "--skip-git-repo-check", "--sandbox", "read-only",
        "--model", model or CODEX_MODEL, "--cd", cwd or os.getcwd(), prompt,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout or CODEX_TIMEOUT,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"codex cli returncode={result.returncode} detail={detail[:300]!r}")
    return result.stdout.strip()


def call_gemma(prompt, num_predict=300, temperature=0.2, timeout=180):
    payload = {
        "model": OLLAMA_MODEL,
        "think": False,  # gemma4は思考型: 無効化しないと空応答になる
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    resp = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=timeout)
    resp.raise_for_status()
    text = resp.json().get("response") or ""
    if not text.strip():
        raise RuntimeError("gemma4 returned empty response")
    return text


def call_with_fallback(prompt, **gemma_kwargs):
    """claude -> codex -> gemma4の順で試し、成功したテキストと使用モデル名を返す。
    全滅したら例外を投げる(呼び出し側でneutral等にフェイルオープンする)。"""
    try:
        return call_claude(prompt), f"claude-{CLAUDE_MODEL}"
    except Exception as exc:
        last = exc
    try:
        return call_codex(prompt), f"codex-{CODEX_MODEL}"
    except Exception as exc:
        last = exc
    try:
        return call_gemma(prompt, **gemma_kwargs), "gemma4-fallback"
    except Exception as exc:
        last = exc
    raise RuntimeError(f"all LLM tiers failed: {last}")
