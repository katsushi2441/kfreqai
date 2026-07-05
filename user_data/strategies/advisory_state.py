"""Shared read/write helpers for the kfreqai LLM advisory state.

Two independent jobs write into the same JSON file:
  - hourly_regime.py (gemma4 via Ollama) writes state["regime"]
  - daily_directive.py (Claude CLI, 3x/day) writes state["directive"]

The freqtrade strategy (user_data/strategies/kurage_advisory_strategy.py)
only reads this file. If it is missing or stale, callers should treat the
advisory as "neutral" (fail-open) rather than blocking trades.
"""
import json
import os
import time

# This file lives in user_data/strategies/, both on the host and (via the
# docker-compose bind mount) inside the freqtrade container as
# /freqtrade/user_data/strategies/. One dirname() up is user_data/, which is
# the shared location both sides read/write.
USER_DATA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(USER_DATA_DIR, "advisory_state.json")
HISTORY_PATH = os.path.join(USER_DATA_DIR, "advisory_history.jsonl")

REGIME_MAX_AGE_SEC = 2 * 60 * 60      # gemma4 runs hourly; stale after 2h
DIRECTIVE_MAX_AGE_SEC = 10 * 60 * 60  # claude runs 3x/day (~8h apart); stale after 10h

VALID_REGIME = {"bullish", "bearish", "neutral"}
VALID_DIRECTIVE = {"risk_on", "risk_off", "neutral"}


def read_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _atomic_write(path, data):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def write_regime(value, note, model):
    if value not in VALID_REGIME:
        raise ValueError("invalid regime value: %r" % value)
    state = read_state()
    entry = {
        "value": value,
        "note": note,
        "model": model,
        "updated_at": time.time(),
        "updated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    state["regime"] = entry
    _atomic_write(STATE_PATH, state)
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "regime", **entry}, ensure_ascii=False) + "\n")
    return entry


def write_directive(value, note, model):
    if value not in VALID_DIRECTIVE:
        raise ValueError("invalid directive value: %r" % value)
    state = read_state()
    entry = {
        "value": value,
        "note": note,
        "model": model,
        "updated_at": time.time(),
        "updated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    state["directive"] = entry
    _atomic_write(STATE_PATH, state)
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "directive", **entry}, ensure_ascii=False) + "\n")
    return entry


def read_recent_history(max_entries=8):
    if not os.path.exists(HISTORY_PATH):
        return []
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()[-max_entries:]
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def is_fresh(entry, max_age_sec):
    if not entry or "updated_at" not in entry:
        return False
    return (time.time() - entry["updated_at"]) < max_age_sec


def effective_regime():
    """Returns 'bullish' | 'bearish' | 'neutral'. Stale/missing -> 'neutral'."""
    state = read_state()
    entry = state.get("regime")
    if is_fresh(entry, REGIME_MAX_AGE_SEC):
        return entry["value"]
    return "neutral"


def effective_directive():
    """Returns 'risk_on' | 'risk_off' | 'neutral'. Stale/missing -> 'neutral'."""
    state = read_state()
    entry = state.get("directive")
    if is_fresh(entry, DIRECTIVE_MAX_AGE_SEC):
        return entry["value"]
    return "neutral"
