"""Shared parameter contract for user-tunable kfreqai strategies.

This is the seam that lets a strategy's numeric knobs be edited from a screen
(the dashboard) instead of by editing code ("vibe coding"). It mirrors the
pattern already used by advisory_state.py:

  - a single JSON file (user_data/strategy_params.json) holds the values,
    namespaced by strategy name
  - the freqtrade strategy only READS its effective params (fail-open to the
    schema defaults if the file is missing / stale / invalid), so a bad or
    absent file never blocks trading
  - the dashboard writes into it via kurage-advisory/advisory_api.py (the
    strategy never sends anything outward; it only reads the local file)

A strategy declares WHICH numbers are tunable as a machine-readable schema
(PARAM_SCHEMA on the strategy class). The dashboard renders one generic form
from that schema — so a new strategy needs a schema declaration, never a
hand-built screen. Only numbers/enums are tunable here; changing entry LOGIC
still requires code (the hypothesis lab or a new strategy).

Schema entry shape (one dict per tunable param):
    {
      "key":   "ema_fast",          # unique within the strategy
      "type":  "int",               # "int" | "float" | "bool" | "enum"
      "default": 12,
      "min": 2, "max": 200, "step": 1,   # int/float only
      "choices": ["a", "b"],             # enum only
      "group": "entry",             # UI grouping
      "label": {"ja": "短期EMA", "en": "Fast EMA"},
      "help":  {"ja": "...", "en": "..."},
    }
"""
import json
import os
import time

USER_DATA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARAMS_PATH = os.path.join(USER_DATA_DIR, "strategy_params.json")

_VALID_TYPES = {"int", "float", "bool", "enum"}


def _read_all():
    """Whole namespaced params file. Missing/corrupt -> {} (fail-open)."""
    try:
        with open(PARAMS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _atomic_write(path, data):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _coerce(spec, value):
    """Coerce + clamp one value against its schema spec.

    Returns (ok, value). ok=False means the incoming value was unusable and
    the caller should fall back to the default.
    """
    t = spec.get("type")
    if t == "bool":
        if isinstance(value, bool):
            return True, value
        if isinstance(value, (int, float)):
            return True, bool(value)
        if isinstance(value, str):
            return True, value.strip().lower() in ("1", "true", "yes", "on")
        return False, None
    if t == "enum":
        choices = spec.get("choices") or []
        return (True, value) if value in choices else (False, None)
    if t in ("int", "float"):
        try:
            num = int(value) if t == "int" else float(value)
        except (TypeError, ValueError):
            return False, None
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and num < lo:
            num = int(lo) if t == "int" else float(lo)
        if hi is not None and num > hi:
            num = int(hi) if t == "int" else float(hi)
        return True, num
    return False, None


def validate_schema(schema):
    """Basic sanity check of a schema list. Raises ValueError on a bad spec.

    Called by the dashboard/API before trusting a strategy's schema; strategies
    can also call it in a test.
    """
    seen = set()
    for spec in schema:
        key = spec.get("key")
        if not key or key in seen:
            raise ValueError("param schema: missing or duplicate key %r" % key)
        seen.add(key)
        if spec.get("type") not in _VALID_TYPES:
            raise ValueError("param schema %s: bad type %r" % (key, spec.get("type")))
        if "default" not in spec:
            raise ValueError("param schema %s: missing default" % key)
        if spec["type"] == "enum" and not spec.get("choices"):
            raise ValueError("param schema %s: enum needs choices" % key)
    return True


def read_params(strategy_name, schema):
    """Effective, validated params for one strategy.

    Always returns a complete dict with every schema key present: the stored
    value when valid and in-range, otherwise the schema default. Never raises —
    a missing/corrupt file or a bad single value degrades to defaults so the
    strategy keeps trading with known-safe numbers.
    """
    stored = _read_all().get(strategy_name, {})
    if not isinstance(stored, dict):
        stored = {}
    out = {}
    for spec in schema:
        key = spec["key"]
        if key in stored:
            ok, val = _coerce(spec, stored[key])
            out[key] = val if ok else spec["default"]
        else:
            out[key] = spec["default"]
    return out


def write_params(strategy_name, updates, schema):
    """Validate `updates` against `schema` and persist into the namespaced file.

    Only keys present in the schema are accepted; each value is coerced/clamped.
    Returns the strategy's full effective params after the merge. This is what
    the dashboard/API calls on save.
    """
    by_key = {s["key"]: s for s in schema}
    clean = {}
    rejected = {}
    for key, value in (updates or {}).items():
        spec = by_key.get(key)
        if spec is None:
            rejected[key] = "unknown key"
            continue
        ok, val = _coerce(spec, value)
        if ok:
            clean[key] = val
        else:
            rejected[key] = "invalid value"
    data = _read_all()
    section = data.get(strategy_name)
    if not isinstance(section, dict):
        section = {}
    section.update(clean)
    data[strategy_name] = section
    data.setdefault("_meta", {})[strategy_name] = {
        "updated_at": time.time(),
        "updated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    _atomic_write(PARAMS_PATH, data)
    return {"effective": read_params(strategy_name, schema), "rejected": rejected}


def schema_for_ui(strategy_name, schema):
    """Schema + current values, ready for the dashboard to render a form."""
    validate_schema(schema)
    current = read_params(strategy_name, schema)
    return {
        "strategy": strategy_name,
        "params": [dict(spec, value=current[spec["key"]]) for spec in schema],
    }
