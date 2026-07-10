#!/usr/bin/env python3
"""①LLM研究員ループ — 実績からClaudeが仮説を立て、バックテストが機械的に裁く日次サイクル。

毎日1回(深夜)実行:
1. ドシエ作成: 直近実績の決済理由/ペア別/時間帯別集計 + ③反省会の教訓 + 検証済み仮説台帳
2. Claude CLI(daily_directive.pyと同じOAuth経路)が「検証可能な仮説」を最大2個、
   制約付きDSL(下記)のJSONで提案する
3. 仮説からvariant戦略ファイルを自動生成(ホワイトリスト化した素性・演算子のみ。
   任意コード生成はさせない)
4. 160銘柄バックテストでベースラインと同一窓比較(窓は週単位でスナップし、
   FreqAIの予測キャッシュを週内で使い回す)
5. 結果をuser_data/lab/hypothesis_ledger.jsonlに記録。基準(損益改善かつDD悪化1pt以内)を
   満たしたら「採用候補」としてメール通知。ライブへの自動反映はしない(人間が判断する)。

仮説DSL:
  {"name": "a-z0-9_", "rationale": "...",
   "kind": "entry_filter",
   "conditions": [{"feature": FEATURES, "op": "<"|"<="|">"|">=", "value": число}, ...]}
  または {"kind": "param_change", "param": "entry_threshold"|"max_recent_pump", "value": число}
"""
import datetime
import json
import os
import re
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_common import BASE_DIR, LAB_DIR, ensure_lab_dir, jsonl_append, jsonl_load, extract_json
from daily_directive import resolve_claude_bin

DB_PATH = os.path.join(BASE_DIR, "user_data", "tradesv3.sqlite")
JOURNAL_PATH = os.path.join(LAB_DIR, "trade_journal.jsonl")
LEDGER_PATH = os.path.join(LAB_DIR, "hypothesis_ledger.jsonl")
HYPO_STRATEGY_PATH = os.path.join(BASE_DIR, "user_data", "strategies", "kfreqai_lab_hypo.py")
BT_CONFIG = "user_data/config_experiment160.json"

CLAUDE_MODEL = os.environ.get("KFREQAI_CLAUDE_MODEL", "sonnet")
CLAUDE_TIMEOUT = int(os.environ.get("KFREQAI_CLAUDE_TIMEOUT", "300"))
NOTIFY_EMAIL = os.environ.get("KFREQAI_LAB_NOTIFY", "katsushi2441@gmail.com")

# 仮説DSLで使える素性(populate_entry_trend内で計算可能なものだけ)
FEATURES = {
    "chg_4h":  'df["close"] / df["close"].shift(48) - 1',
    "chg_1h":  'df["close"] / df["close"].shift(12) - 1',
    "rsi_14":  'ta.RSI(df, timeperiod=14)',
    "hour_utc": 'df["date"].dt.hour',
    "vol_ratio": 'df["volume"] / df["volume"].rolling(288).mean()',
    "pred":    'df["&-s_close"]',
}
OPS = {"<", "<=", ">", ">="}
PARAMS = {"entry_threshold": (0.003, 0.03), "max_recent_pump": (0.03, 0.5)}


def build_dossier(conn):
    conn.row_factory = sqlite3.Row
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=7)).isoformat()[:19]
    rows = conn.execute(
        "SELECT pair, close_profit, exit_reason, open_date FROM trades"
        " WHERE is_open=0 AND close_date > ?", (cutoff,)).fetchall()
    by_reason, by_hour = {}, {}
    for r in rows:
        by_reason.setdefault(r["exit_reason"], []).append(r["close_profit"])
        h = int(r["open_date"][11:13])
        by_hour.setdefault(h, []).append(r["close_profit"])
    dossier = {"period": "直近7日", "closed": len(rows)}
    dossier["exit_reasons"] = {
        k: {"n": len(v), "sum_pct": round(sum(v) * 100, 1),
            "win": round(100 * sum(1 for x in v if x > 0) / len(v))}
        for k, v in by_reason.items()}
    dossier["by_entry_hour_utc"] = {
        h: {"n": len(v), "sum_pct": round(sum(v) * 100, 1)}
        for h, v in sorted(by_hour.items()) if len(v) >= 2}
    lessons = jsonl_load(JOURNAL_PATH, limit=40)
    dossier["postmortem_categories"] = {}
    for l in lessons:
        c = l.get("category", "other")
        d = dossier["postmortem_categories"].setdefault(c, {"n": 0, "sum_pct": 0.0})
        d["n"] += 1
        d["sum_pct"] = round(d["sum_pct"] + (l.get("result_pct") or 0), 1)
    tested = jsonl_load(LEDGER_PATH, limit=30)
    dossier["already_tested"] = [
        {"name": t["hypothesis"].get("name"), "delta_usdt": t.get("delta_usdt"),
         "verdict": t.get("verdict")} for t in tested if t.get("hypothesis")]
    return dossier


def call_claude(prompt):
    claude_bin = resolve_claude_bin()
    if not claude_bin:
        raise RuntimeError("claude binary not found")
    cmd = [claude_bin, "-p", "--output-format", "text", "--tools", "",
           "--model", CLAUDE_MODEL, prompt]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT)
    if result.returncode != 0:
        raise RuntimeError(f"claude cli rc={result.returncode}: "
                           f"{(result.stderr or result.stdout)[:300]!r}")
    return result.stdout.strip()


def propose_hypotheses(dossier):
    prompt = f"""あなたは暗号資産自動売買ボット(FreqAI/LightGBM、160銘柄、5分足、ロングのみ)の
戦略研究員です。以下は実測データです。

{json.dumps(dossier, ensure_ascii=False, indent=1)}

現在の戦略: 2時間先予測が+1%超でエントリー。過熱フィルター(直前4hで+10%超は見送り)導入済み。
roi利確3%/1.5%/0%(2h)、損切り-5%(成行)。

このデータから、バックテストで検証する価値のある仮説を最大2個、次のJSONだけで提案してください。
検証済み仮説(already_tested)と同じものは提案しない。データに根拠のない思いつきは書かない。

{{"hypotheses": [
  {{"name": "英小文字とアンダースコアのみ", "rationale": "データ上の根拠を1文で",
    "kind": "entry_filter",
    "conditions": [{{"feature": "chg_4h|chg_1h|rsi_14|hour_utc|vol_ratio|pred", "op": "<|<=|>|>=", "value": 数値}}]}}
  または
  {{"name": "...", "rationale": "...", "kind": "param_change",
    "param": "entry_threshold|max_recent_pump", "value": 数値}}
]}}
entry_filterのconditionsは「これを全て満たす場合にエントリーを見送る」条件。最大2条件。
【単位に注意】chg_4h/chg_1h/pred/entry_threshold/max_recent_pumpは小数表記
(0.05 = 5%。「5」と書くと500%の意味になり絶対に成立しない)。
rsi_14は0〜100、hour_utcは0〜23の整数、vol_ratioは平均比の倍率(1.0=平均並み)。
"""
    raw = call_claude(prompt)
    parsed = extract_json(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"仮説JSONのパース失敗: {raw[:200]!r}")
    return parsed.get("hypotheses") or []


def sanitize_name(h, index):
    """仮説名を安全な識別子に正規化する(LLMが日本語名を付けても却下しない)。"""
    name = re.sub(r"[^a-z0-9_]", "", str(h.get("name", "")).lower())
    if len(name) < 3:
        name = f"hypo_{datetime.date.today():%m%d}_{index}"
    h["name"] = name[:40]


_FRACTION_FEATURES = {"chg_4h", "chg_1h", "pred"}


def normalize_units(h):
    """%表記と小数表記の取り違えを自動補正する(1超の値は%とみなして/100)。
    LLMは単位を明記しても間違えることがある(実測: chg_1h>5 を+5%のつもりで提案)。"""
    if h.get("kind") == "entry_filter":
        for c in h.get("conditions") or []:
            if (c.get("feature") in _FRACTION_FEATURES
                    and isinstance(c.get("value"), (int, float))
                    and abs(c["value"]) > 1):
                c["value"] = round(c["value"] / 100, 4)
                h["rationale"] = (h.get("rationale", "") + " [値を%→小数に自動補正]")
    if (h.get("kind") == "param_change"
            and isinstance(h.get("value"), (int, float)) and h["value"] > 1):
        h["value"] = round(h["value"] / 100, 4)
        h["rationale"] = (h.get("rationale", "") + " [値を%→小数に自動補正]")


def validate(h):
    """ホワイトリスト検証。通らない仮説は破棄(任意コードを実行させない)。"""
    if h.get("kind") == "entry_filter":
        conds = h.get("conditions") or []
        if not 1 <= len(conds) <= 2:
            return "bad conditions count"
        for c in conds:
            if c.get("feature") not in FEATURES or c.get("op") not in OPS:
                return "bad feature/op"
            if not isinstance(c.get("value"), (int, float)):
                return "bad value"
        return None
    if h.get("kind") == "param_change":
        p = h.get("param")
        if p not in PARAMS or not isinstance(h.get("value"), (int, float)):
            return "bad param"
        lo, hi = PARAMS[p]
        if not lo <= h["value"] <= hi:
            return f"value out of range {lo}-{hi}"
        return None
    return "bad kind"


def generate_variant(h):
    """検証済み仮説からvariant戦略ファイルを生成する(既知プリミティブの合成のみ)。"""
    lines = [
        '"""research_daily.pyが自動生成した検証用variant。ライブでは使わない。',
        f'仮説: {h["name"]} — {h.get("rationale", "")}"""',
        "import talib.abstract as ta",
        "from kurage_freqai_strategy import KurageFreqAIStrategy",
        "",
        "",
        "class KfreqaiLabHypo(KurageFreqAIStrategy):",
    ]
    if h["kind"] == "param_change" and h["param"] == "max_recent_pump":
        lines.append(f"    max_recent_pump = {h['value']}")
        lines.append("")
    elif h["kind"] == "param_change" and h["param"] == "entry_threshold":
        lines += [
            "    from freqtrade.strategy import DecimalParameter",
            f"    entry_threshold = DecimalParameter(0.003, 0.03, default={h['value']},"
            " space='buy', optimize=True, load=False)",
            "",
        ]
    if h["kind"] == "entry_filter":
        lines += [
            "    def populate_entry_trend(self, df, metadata):",
            "        df = super().populate_entry_trend(df, metadata)",
        ]
        for i, c in enumerate(h["conditions"]):
            lines.append(f"        f{i} = {FEATURES[c['feature']]}")
        mask = " & ".join(f"(f{i} {c['op']} {c['value']})"
                          for i, c in enumerate(h["conditions"]))
        lines += [
            f"        df.loc[{mask}, 'enter_long'] = 0",
            "        return df",
        ]
    if len(lines) == 7:  # クラス本体が空(param_changeのみでbody無し)を防ぐ
        lines.append("    pass")
    with open(HYPO_STRATEGY_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def week_window():
    """週単位でスナップした検証窓(週内はFreqAI予測キャッシュを使い回すため)。
    テストや手動検証ではKFREQAI_LAB_TIMERANGEで上書きできる。"""
    override = os.environ.get("KFREQAI_LAB_TIMERANGE")
    if override:
        return override
    today = datetime.date.today()
    end = today - datetime.timedelta(days=today.weekday())  # 直近の月曜
    start = end - datetime.timedelta(days=30)
    return f"{start:%Y%m%d}-{end:%Y%m%d}"


def run_backtest(strategy, timerange):
    cmd = ["docker", "compose", "run", "--rm",
           "--name", f"kfreqai-lab-{strategy.lower()}"[:60],
           "freqtrade", "backtesting",
           "--config", BT_CONFIG, "--strategy", strategy,
           "--freqaimodel", "LightGBMRegressor", "--timerange", timerange]
    result = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True, timeout=7200)
    m = re.search(
        rf"\s{strategy}\s.*?│\s+(\d+)\s+│.*?│\s+(-?[\d.]+)\s+│\s+(-?[\d.]+)\s+│",
        result.stdout)
    if not m:
        raise RuntimeError(f"バックテスト結果のパース失敗 rc={result.returncode}: "
                           f"{result.stdout[-400:]!r}")
    return {"trades": int(m.group(1)), "profit_abs": float(m.group(2)),
            "profit_pct": float(m.group(3))}


def baseline_result(timerange):
    """同一窓のベースライン結果を台帳から探し、無ければ実測する。"""
    for r in jsonl_load(LEDGER_PATH):
        if r.get("type") == "baseline" and r.get("timerange") == timerange:
            return r["result"]
    res = run_backtest("KurageFreqAIStrategy", timerange)
    jsonl_append(LEDGER_PATH, {"type": "baseline", "timerange": timerange,
                               "at": datetime.datetime.now().isoformat()[:19],
                               "result": res})
    return res


def notify(subject, body):
    try:
        import smtplib
        import ssl
        from email.mime.text import MIMEText
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = os.environ["SMTP_FROM"]
        msg["To"] = NOTIFY_EMAIL
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST", "mail18.heteml.jp"),
                              int(os.environ.get("SMTP_PORT", 465)), context=ctx) as s:
            s.login(os.environ["SMTP_FROM"], os.environ["SMTP_PASSWORD"])
            s.sendmail(os.environ["SMTP_FROM"], [NOTIFY_EMAIL], msg.as_bytes())
    except Exception as e:
        print(f"[research] メール通知失敗(処理は続行): {str(e)[:100]}")


def main():
    ensure_lab_dir()
    conn = sqlite3.connect(DB_PATH)
    dossier = build_dossier(conn)
    print("[research] ドシエ:", json.dumps(dossier, ensure_ascii=False)[:400])

    hypotheses = propose_hypotheses(dossier)
    print(f"[research] Claude提案: {len(hypotheses)}件")

    timerange = week_window()
    base = baseline_result(timerange)
    print(f"[research] baseline({timerange}): {base}")

    for i, h in enumerate(hypotheses[:2]):
        sanitize_name(h, i)
        normalize_units(h)
        err = validate(h)
        if err:
            print(f"[research] 却下({err}): {json.dumps(h, ensure_ascii=False)[:150]}")
            jsonl_append(LEDGER_PATH, {"type": "rejected", "reason": err, "hypothesis": h,
                                       "at": datetime.datetime.now().isoformat()[:19]})
            continue
        generate_variant(h)
        try:
            res = run_backtest("KfreqaiLabHypo", timerange)
        except Exception as e:
            print(f"[research] {h['name']} バックテスト失敗: {str(e)[:200]}")
            continue
        delta = round(res["profit_abs"] - base["profit_abs"], 1)
        verdict = "candidate" if delta > 0 else "worse"
        record = {"type": "hypothesis", "timerange": timerange,
                  "at": datetime.datetime.now().isoformat()[:19],
                  "hypothesis": h, "result": res, "baseline": base,
                  "delta_usdt": delta, "verdict": verdict}
        jsonl_append(LEDGER_PATH, record)
        print(f"[research] {h['name']}: {res['profit_abs']} USDT "
              f"(baseline {base['profit_abs']}, Δ{delta:+}) → {verdict}")
        if verdict == "candidate":
            notify(f"[kfreqai研究員] 採用候補: {h['name']} (Δ{delta:+} USDT)",
                   json.dumps(record, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
