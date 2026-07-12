#!/usr/bin/env python3
"""3x/day risk directive for kfreqai, using the Claude CLI (OAuth session).

Runs at 05:00 / 13:00 / 21:00 JST. Reviews the last ~8 hours of gemma4
regime calls plus current price stats, and asks an LLM to give a coarse
risk directive (risk_on / risk_off / neutral) for the freqtrade strategy
to read. This only ever runs live/forward -- it cannot be backtested,
since the LLM's own knowledge already contains history past any given
backtest date.

3-tier fallback (2026-07-13追加。claude CLIがOAuthセッション上限で失敗する
実例が過去に発生したため -- 07-11 21:01 "You've hit your session limit"):
  1. Claude CLI (claude binary, OAuth) -- resolve_claude_bin()
  2. Codex CLI (codex binary, model gpt-5.6-sol, OAuth) -- resolve_codex_bin()
  3. gemma4:12b-it-qat (ローカルOllama、クォータ無し) -- lab_common.ollama_generate
すべて失敗したら従来通りneutralにフェイルオープン。
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "user_data", "strategies"))
import advisory_state
from hourly_regime import load_config, fetch_ohlcv, build_stats_block
from lab_common import ollama_generate

CLAUDE_MODEL = os.environ.get("KFREQAI_CLAUDE_MODEL", "sonnet")
CLAUDE_TIMEOUT = int(os.environ.get("KFREQAI_CLAUDE_TIMEOUT", "180"))
CODEX_MODEL = os.environ.get("KFREQAI_CODEX_MODEL", "gpt-5.6-sol")
CODEX_TIMEOUT = int(os.environ.get("KFREQAI_CODEX_TIMEOUT", "180"))


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


def build_prompt(stats_block, history):
    history_lines = []
    for h in history:
        if h.get("type") != "regime":
            continue
        history_lines.append(f"{h.get('updated_at_iso', '?')}: {h.get('value')} ({h.get('note', '')})")
    history_block = "\n".join(history_lines) or "（履歴なし）"

    return f"""あなたは暗号資産の自動売買botのリスク管理アドバイザーです。
これは紙上取引(dry-run、実資金は動いていません)のbotです。

直近の主要銘柄の価格変化率:
{stats_block}

直近8時間の地合い判定の推移(ローカルの軽量モデルによる自動判定):
{history_block}

これらを踏まえて、この先数時間のリスク方針を判定してください。
- risk_on: 通常通り取引を継続してよい
- risk_off: 相場が不安定/下落基調で、新規エントリーは見合わせるべき
- neutral: 判断材料が不十分、通常運用でよい

出力は必ず以下の2行だけの形式で、他の文章は書かないでください。
DIRECTIVE: risk_on または risk_off または neutral のいずれか一語
NOTE: 40文字以内の日本語の一言理由
"""


def parse_response(text):
    directive_match = re.search(r"DIRECTIVE:\s*(risk_on|risk_off|neutral)", text, re.IGNORECASE)
    note_match = re.search(r"NOTE:\s*(.+)", text)
    directive = directive_match.group(1).lower() if directive_match else "neutral"
    note = note_match.group(1).strip()[:150] if note_match else ""
    return directive, note


def call_claude(prompt):
    claude_bin = resolve_claude_bin()
    if not claude_bin:
        raise RuntimeError("claude binary not found")
    cmd = [
        claude_bin,
        "-p",
        "--output-format", "text",
        "--tools", "",
        "--model", CLAUDE_MODEL,
        prompt,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"claude cli returncode={result.returncode} detail={detail[:300]!r}")
    return result.stdout.strip()


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


def call_codex(prompt):
    codex_bin = resolve_codex_bin()
    if not codex_bin:
        raise RuntimeError("codex binary not found")
    cmd = [
        codex_bin,
        "exec",
        "--skip-git-repo-check",
        "--sandbox", "read-only",
        "--model", CODEX_MODEL,
        "--cd", os.path.dirname(os.path.abspath(__file__)),
        prompt,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=CODEX_TIMEOUT,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"codex cli returncode={result.returncode} detail={detail[:300]!r}")
    return result.stdout.strip()


def call_gemma(prompt):
    text = ollama_generate(prompt, num_predict=200, temperature=0.2, timeout=120)
    if not text.strip():
        raise RuntimeError("gemma4 returned empty response")
    return text


def main():
    config = load_config()
    exchange_name = config["exchange"]["name"]
    pairs = config["exchange"]["pair_whitelist"]

    ohlcv_by_pair = fetch_ohlcv(exchange_name, pairs)
    stats_block = build_stats_block(ohlcv_by_pair)
    history = advisory_state.read_recent_history(max_entries=12)

    prompt = build_prompt(stats_block, history)

    directive = note = model_used = None
    try:
        response_text = call_claude(prompt)
        directive, note = parse_response(response_text)
        model_used = f"claude-{CLAUDE_MODEL}"
    except Exception as exc:
        print(f"[daily_directive] claude call failed, trying codex: {exc}", flush=True)
        try:
            response_text = call_codex(prompt)
            directive, note = parse_response(response_text)
            model_used = f"codex-{CODEX_MODEL}"
        except Exception as exc2:
            print(f"[daily_directive] codex call failed, trying gemma4: {exc2}", flush=True)
            try:
                response_text = call_gemma(prompt)
                directive, note = parse_response(response_text)
                model_used = "gemma4-fallback"
            except Exception as exc3:
                print(f"[daily_directive] gemma4 call failed, defaulting to neutral: {exc3}", flush=True)
                directive, note = "neutral", "claude/codex/gemma4いずれも呼び出し失敗のためneutral"
                model_used = "none-failopen"

    entry = advisory_state.write_directive(directive, note, model_used)
    print(f"[daily_directive] {entry['updated_at_iso']} directive={directive} note={note!r}", flush=True)


if __name__ == "__main__":
    main()
