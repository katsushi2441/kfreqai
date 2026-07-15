"""backtest_jobs — judgment_api用のバックテスト・ジョブキュー。

仮説DSL(JSON)を受け取り、variant戦略コードを自動生成して主要13銘柄で
バックテストし、ベースライン(KurageFreqAIStrategy)との差分を返す。
バイブコーディングの「仮説→コード→検証→結果」ループの検証部分。

安全設計:
- 受け付けるのは仮説DSLのみ(research_daily.pyと同じホワイトリスト検証)。
  任意のPythonコードは受け取らない・実行しない。
- 生成ファイルは kfreqai_api_hypo.py / class KfreqaiApiHypo に固定
  (research_dailyのKfreqaiLabHypoとは別ファイルにして夜間ループと衝突させない)。
- advisory_state.json / news_signals.json は空JSONをシャドウマウントし、
  「今の」地合い判定が過去窓のバックテストに漏れないようにする(決定的な結果)。

実行はワーカースレッド1本で直列(CPU負荷制御 + variantファイル共有のため)。
ベースラインを先に流すことでFreqAIの予測キャッシュが温まり、仮説側の実行は
大幅に短くなる。ベースライン結果は timerange 単位でキャッシュする。
"""
import datetime
import json
import os
import queue
import re
import subprocess
import threading
import uuid

from lab_common import BASE_DIR, LAB_DIR, jsonl_append, jsonl_load
import judgment_backend

BT_CONFIG = "user_data/config_backtest_api.json"
BASELINE_STRATEGY = "KurageFreqAIStrategy"
HYPO_STRATEGY = "KfreqaiApiHypo"
HYPO_STRATEGY_PATH = os.path.join(BASE_DIR, "user_data", "strategies", "kfreqai_api_hypo.py")
NEUTRAL_DIR = os.path.join(BASE_DIR, "user_data", "lab", "bt_neutral")
JOBS_DIR = os.path.join(LAB_DIR, "backtest_jobs")
BASELINE_CACHE = os.path.join(LAB_DIR, "backtest_api_baselines.jsonl")

MAX_WINDOW_DAYS = 60
MAX_QUEUED = 10
BT_TIMEOUT_SEC = 7200

_queue = queue.Queue()
_jobs = {}          # job_id -> dict (最新状態はファイルにも書く)
_jobs_lock = threading.Lock()


# ---------------------------------------------------------------------------
# job storage
# ---------------------------------------------------------------------------

def _save(job):
    os.makedirs(JOBS_DIR, exist_ok=True)
    path = os.path.join(JOBS_DIR, f"{job['id']}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _load_all():
    if not os.path.isdir(JOBS_DIR):
        return
    for fn in os.listdir(JOBS_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(JOBS_DIR, fn), encoding="utf-8") as f:
                job = json.load(f)
            _jobs[job["id"]] = job
        except Exception:
            continue


def get_job(job_id):
    with _jobs_lock:
        return _jobs.get(job_id)


def list_jobs(limit=20):
    with _jobs_lock:
        jobs = sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)
    return [{k: j.get(k) for k in ("id", "status", "created_at", "timerange",
                                    "hypothesis", "delta_usdt", "verdict", "error")}
            for j in jobs[:limit]]


# ---------------------------------------------------------------------------
# timerange
# ---------------------------------------------------------------------------

def default_timerange():
    """直近の月曜で終わる30日窓(research_dailyのweek_windowと同じスナップ)。"""
    today = datetime.date.today()
    end = today - datetime.timedelta(days=today.weekday())
    start = end - datetime.timedelta(days=30)
    return f"{start:%Y%m%d}-{end:%Y%m%d}"


def validate_timerange(tr):
    m = re.fullmatch(r"(\d{8})-(\d{8})", tr)
    if not m:
        return "timerange must be YYYYMMDD-YYYYMMDD"
    try:
        start = datetime.datetime.strptime(m.group(1), "%Y%m%d").date()
        end = datetime.datetime.strptime(m.group(2), "%Y%m%d").date()
    except ValueError:
        return "invalid date in timerange"
    if start >= end:
        return "timerange start must be before end"
    if (end - start).days > MAX_WINDOW_DAYS:
        return f"window too long (max {MAX_WINDOW_DAYS} days)"
    if end > datetime.date.today():
        return "timerange end must not be in the future"
    return None


# ---------------------------------------------------------------------------
# backtest execution
# ---------------------------------------------------------------------------

def _run_backtest(strategy, timerange, name_suffix):
    cmd = ["docker", "compose", "run", "--rm",
           "--name", f"kfreqai-btapi-{name_suffix}"[:60],
           "-v", f"{NEUTRAL_DIR}/advisory_state.json:/freqtrade/user_data/advisory_state.json:ro",
           "-v", f"{NEUTRAL_DIR}/news_signals.json:/freqtrade/user_data/news_signals.json:ro",
           "freqtrade", "backtesting",
           "--config", BT_CONFIG, "--strategy", strategy,
           "--freqaimodel", "LightGBMRegressor", "--timerange", timerange]
    result = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True,
                            timeout=BT_TIMEOUT_SEC)
    m = re.search(
        rf"\s{strategy}\s.*?│\s+(\d+)\s+│.*?│\s+(-?[\d.]+)\s+│\s+(-?[\d.]+)\s+│",
        result.stdout)
    if not m:
        raise RuntimeError(f"backtest parse failed rc={result.returncode}: "
                           f"{(result.stderr or result.stdout)[-300:]!r}")
    return {"trades": int(m.group(1)), "profit_abs": float(m.group(2)),
            "profit_pct": float(m.group(3))}


def _baseline_result(timerange, job_id):
    for r in jsonl_load(BASELINE_CACHE):
        if r.get("timerange") == timerange and r.get("config") == BT_CONFIG:
            return r["result"]
    res = _run_backtest(BASELINE_STRATEGY, timerange, f"base-{job_id}")
    jsonl_append(BASELINE_CACHE, {
        "timerange": timerange, "config": BT_CONFIG,
        "at": datetime.datetime.now().isoformat()[:19], "result": res})
    return res


def _write_variant(hypothesis):
    code = judgment_backend.build_variant_strategy_code(hypothesis)
    # research_dailyのKfreqaiLabHypoとクラス名・ファイルを分ける(同名クラス2つは
    # freqtradeの戦略探索で衝突するため)
    code = code.replace("class KfreqaiLabHypo(", f"class {HYPO_STRATEGY}(")
    code = code.replace("research_daily.pyが自動生成した", "judgment_api(/v1/backtest)が自動生成した")
    with open(HYPO_STRATEGY_PATH, "w", encoding="utf-8") as f:
        f.write(code)


# ---------------------------------------------------------------------------
# worker
# ---------------------------------------------------------------------------

def _set(job, **kw):
    with _jobs_lock:
        job.update(kw)
        _save(job)


def _worker():
    while True:
        job = _queue.get()
        _set(job, status="running", started_at=datetime.datetime.now().isoformat()[:19])
        try:
            base = _baseline_result(job["timerange"], job["id"])
            _set(job, baseline=base)
            _write_variant(job["hypothesis"])
            res = _run_backtest(HYPO_STRATEGY, job["timerange"], job["id"])
            delta = round(res["profit_abs"] - base["profit_abs"], 1)
            verdict = "candidate" if delta > 0 else "worse"
            _set(job, status="done", result=res, delta_usdt=delta, verdict=verdict,
                 finished_at=datetime.datetime.now().isoformat()[:19])
        except Exception as exc:
            _set(job, status="failed", error=str(exc)[:500],
                 finished_at=datetime.datetime.now().isoformat()[:19])


def submit(hypothesis, timerange):
    with _jobs_lock:
        queued = sum(1 for j in _jobs.values() if j["status"] in ("queued", "running"))
    if queued >= MAX_QUEUED:
        raise RuntimeError(f"queue full ({queued} jobs pending)")
    job = {"id": uuid.uuid4().hex[:12], "status": "queued",
           "created_at": datetime.datetime.now().isoformat()[:19],
           "hypothesis": hypothesis, "timerange": timerange,
           "baseline": None, "result": None, "delta_usdt": None,
           "verdict": None, "error": None}
    with _jobs_lock:
        _jobs[job["id"]] = job
        _save(job)
    _queue.put(job)
    return job


def start():
    """judgment_apiのimport時に一度だけ呼ぶ。過去ジョブを復元し、ワーカーを起動する。"""
    _load_all()
    with _jobs_lock:
        for job in _jobs.values():
            if job["status"] == "running":
                # サービス再起動で中断された
                job["status"] = "failed"
                job["error"] = "service restarted while running; resubmit"
                _save(job)
            elif job["status"] == "queued":
                _queue.put(job)
    t = threading.Thread(target=_worker, daemon=True, name="backtest-worker")
    t.start()
