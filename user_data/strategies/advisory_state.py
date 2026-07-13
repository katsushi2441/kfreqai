"""Shared read/write helpers for the kfreqai LLM advisory state.

Three independent jobs write into the same JSON file:
  - hourly_regime.py (gemma4 via Ollama) writes state["regime"] and
    state["strength"] (per-pair relative strength vs the pair basket)
  - daily_directive.py (Claude CLI, 3x/day) writes state["directive"]

The freqtrade strategy (user_data/strategies/kurage_advisory_strategy.py /
kurage_freqai_strategy.py) only reads this file. If it is missing or stale,
callers should treat the advisory as "neutral" (fail-open) rather than
blocking trades.

state["strength"] lets a bearish overall regime be overridden per pair:
a pair that is meaningfully outperforming the basket average can still be
entered even while the market-wide regime is bearish, instead of the old
all-or-nothing gate. directive=risk_off remains a hard override regardless
of strength (that signal is deliberate/considered, 3x/day via Claude).

Dashboard visibility: kurage_web/kfreqai.php はheteml上の別ホストで動いて
おり、このホストのファイルを直接読めない。regime/directiveをダッシュボード
に見せる用途は kurage-advisory/advisory_api.py (FastAPI, 127.0.0.1:18320,
既存の18314 nginx配下にプロキシ) が担う。kfreqai.php側からAPI越しに読む
設計なので、ここではファイルへの書き込みだけを行い、外部への送信は一切
行わない。
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
STRENGTH_MAX_AGE_SEC = 2 * 60 * 60    # computed alongside regime; stale after 2h

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


def write_strength(pairs_data, market_avg_4h, model):
    """pairs_data: {pair: {"chg_4h": float, "rel_4h": float}}.

    rel_4h is the pair's 4h % change minus market_avg_4h (percentage
    points): positive means the pair is outperforming the basket.
    """
    state = read_state()
    entry = {
        "pairs": pairs_data,
        "market_avg_4h": market_avg_4h,
        "model": model,
        "updated_at": time.time(),
        "updated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    state["strength"] = entry
    _atomic_write(STATE_PATH, state)
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        top = sorted(pairs_data.items(), key=lambda kv: -kv[1]["rel_4h"])[:3]
        top_note = ", ".join(f"{p}:{d['rel_4h']:+.1f}" for p, d in top)
        f.write(json.dumps({
            "type": "strength", "market_avg_4h": market_avg_4h, "top": top_note,
            "updated_at": entry["updated_at"], "updated_at_iso": entry["updated_at_iso"],
        }, ensure_ascii=False) + "\n")
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


def effective_relative_strength(pair):
    """Returns the pair's rel_4h (percentage points vs basket average),
    or None if the strength data is stale/missing/doesn't cover this pair.
    None must be treated as "no override available" (fail closed on the
    override, not fail open) since it means we can't tell if the pair is
    actually strong.
    """
    state = read_state()
    entry = state.get("strength")
    if not is_fresh(entry, STRENGTH_MAX_AGE_SEC):
        return None
    data = (entry.get("pairs") or {}).get(pair)
    if not data:
        return None
    return data.get("rel_4h")
