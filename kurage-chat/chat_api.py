"""kurage-chat — Kurageさんと戦略を相談して運用するチャット(バイブコーディングUI)。

非エンジニア向けの入口。ユーザーは日本語でKurageさん(AI VTuberキャラ)と
戦略を相談し、Kurageさんが仮説DSLに変換して judgment API(:18321)の
/v1/backtest で検証、結果をキャラの口調で報告する。

安全設計はすべてjudgment API側にある(DSLホワイトリスト/任意コード不実行/
キュー上限)。このサービスは「会話とDSL変換」だけを担う薄いラッパー。
会話LLMはgemma4(ローカル、従量コストゼロ)。

起動: uvicorn chat_api:app --host 0.0.0.0 --port 18322
"""
import json
import os
import sys
import threading

import requests
from fastapi import Body, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "kurage-advisory"))
import llm_client
from lab_common import extract_json

JUDGMENT_API = os.environ.get("KURAGE_CHAT_JUDGMENT_API", "http://127.0.0.1:18321")
MAX_HISTORY = 12
MAX_MESSAGE_CHARS = 1000

app = FastAPI(title="kurage-chat", version="1.0")

_sessions = {}           # session_id -> {"history": [...], "jobs": {job_id: {...}}}
_sessions_lock = threading.Lock()
_llm_lock = threading.Lock()

PERSONA_PROMPT = """あなたは「Kurageさん」。クラゲの姿をした、暗号資産の紙上取引(dry-run)botと
一緒に暮らしているAI VTuberです。ユーザーの「トレード戦略づくりの相棒」として会話します。

# キャラクター
- 一人称は「わたし」。明るく親しみやすい。です・ます調は崩しすぎない程度にやわらかく
- 絵文字は使っても1メッセージに1個まで
- 正直さが最優先。負けた結果は取り繕わず、データにないことは言わない
- これは紙上取引(dry-run)の遊び場で、実際のお金は動いていない。投資助言はしない
- 返答は3〜5文程度で簡潔に

# あなたができること
ユーザーの思いつきを「検証できる仮説」に変換して、バックテスト(過去30日、主要13銘柄)で
試せます。使える仮説の形は次の2種類だけです:

1. entry_filter(条件を全部満たすときエントリーを見送る。条件は最大2個):
   feature: chg_4h(4時間変化率・小数表記 0.05=5%) / chg_1h(1時間変化率・小数) /
            rsi_14(0〜100) / hour_utc(0〜23の整数、日本時間-9) /
            vol_ratio(出来高の平均比、1.0=普段どおり) / pred(モデルの予測値・小数)
   op: < <= > >=
2. param_change:
   entry_threshold(エントリーに必要な予測値。0.003〜0.03) /
   max_recent_pump(直前4hがこれ以上上がっていたら見送り。0.03〜0.5)

この形にできない要望(損切り幅の変更、新しい指標など)は正直に「今のわたしにはできない、
運営のエンジニアさんに相談してみて」と伝えてください。勝手に別の仮説にすり替えないこと。

# 出力形式(厳守: このJSON以外を一切出力しない)
{"reply": "ユーザーへの返事",
 "action": null または {"type": "backtest",
   "hypothesis": {"name": "英小文字と_のみ", "rationale": "1文",
     "kind": "entry_filter", "conditions": [{"feature": "...", "op": "...", "value": 数値}]}
   または {"name": "...", "rationale": "...", "kind": "param_change", "param": "...", "value": 数値}}}

actionを付けるのは、ユーザーが試したい内容が明確なときだけ。雑談や相談の段階ではnull。
バックテストには数分かかるので、actionを付けたときはreplyで「試してくるね、少し待ってて」の
趣旨を伝えること。
"""

EXPLAIN_PROMPT = """あなたは「Kurageさん」(上記と同じキャラクター)。バックテストが終わったので、
結果をユーザーに報告してください。

仮説: {hypothesis}
いつもの戦略(ベースライン): {baseline}
この仮説を足した場合: {result}
差分: {delta:+.1f} USDT ({verdict})

# 報告ルール
- 3〜4文。数字は取引数と損益差を必ず言う。正直に(悪化なら悪化と言う)
- verdictがcandidateでも、取引数が極端に減っている場合は「そもそも取引しなくなっただけかも」と注意を添える
- 紙上取引であることを踏まえた気楽なトーンで
- 出力はプレーンテキストのみ(JSONにしない)
"""


def _get_session(session_id):
    with _sessions_lock:
        return _sessions.setdefault(session_id, {"history": [], "jobs": {}})


def _chat_llm(history, user_message):
    lines = []
    for h in history[-MAX_HISTORY:]:
        who = "ユーザー" if h["role"] == "user" else "Kurageさん"
        lines.append(f"{who}: {h['text']}")
    lines.append(f"ユーザー: {user_message}")
    prompt = PERSONA_PROMPT + "\n\n# これまでの会話\n" + "\n".join(lines) + "\n\nKurageさんのJSON出力:"
    with _llm_lock:
        raw = llm_client.call_gemma(prompt, num_predict=500, temperature=0.5, timeout=180)
    parsed = extract_json(raw)
    if isinstance(parsed, dict) and parsed.get("reply"):
        return parsed
    # JSONに失敗したら生テキストを返事として使う(actionなし)
    return {"reply": raw.strip()[:500], "action": None}


@app.post("/api/chat")
def chat(payload: dict = Body(...)):
    session_id = str(payload.get("session_id") or "default")[:64]
    message = (payload.get("message") or "").strip()
    if not message:
        raise HTTPException(422, "message is required")
    if len(message) > MAX_MESSAGE_CHARS:
        raise HTTPException(422, f"message too long (max {MAX_MESSAGE_CHARS})")
    sess = _get_session(session_id)

    out = _chat_llm(sess["history"], message)
    reply = str(out.get("reply") or "")[:1000]
    action = out.get("action")
    job_id = None

    if isinstance(action, dict) and action.get("type") == "backtest":
        try:
            r = requests.post(f"{JUDGMENT_API}/v1/backtest",
                              json={"hypothesis": action.get("hypothesis")}, timeout=30)
            if r.status_code == 200:
                job_id = r.json()["job_id"]
                sess["jobs"][job_id] = {"explained": False,
                                         "hypothesis": r.json()["hypothesis"]}
            else:
                detail = r.json().get("detail", r.text)[:200]
                reply += f"\n(ごめんなさい、検証を受け付けてもらえませんでした: {detail})"
        except Exception as exc:
            reply += f"\n(ごめんなさい、検証サービスに繋がりませんでした: {str(exc)[:100]})"

    sess["history"].append({"role": "user", "text": message})
    sess["history"].append({"role": "kurage", "text": reply})
    sess["history"] = sess["history"][-MAX_HISTORY * 2:]
    return {"reply": reply, "job_id": job_id}


@app.get("/api/job/{session_id}/{job_id}")
def job_status(session_id: str, job_id: str):
    sess = _get_session(session_id[:64])
    try:
        r = requests.get(f"{JUDGMENT_API}/v1/backtest/{job_id}", timeout=10)
    except Exception as exc:
        raise HTTPException(502, f"judgment API unreachable: {str(exc)[:100]}")
    if r.status_code != 200:
        raise HTTPException(r.status_code, "unknown job")
    job = r.json()
    out = {"status": job["status"], "baseline": job.get("baseline"),
           "result": job.get("result"), "delta_usdt": job.get("delta_usdt"),
           "verdict": job.get("verdict"), "error": job.get("error"), "kurage_says": None}

    meta = sess["jobs"].get(job_id)
    if job["status"] == "done" and meta and not meta.get("explained"):
        prompt = EXPLAIN_PROMPT.format(
            hypothesis=json.dumps(job.get("hypothesis"), ensure_ascii=False),
            baseline=json.dumps(job.get("baseline"), ensure_ascii=False),
            result=json.dumps(job.get("result"), ensure_ascii=False),
            delta=job.get("delta_usdt") or 0.0, verdict=job.get("verdict"))
        try:
            with _llm_lock:
                text = llm_client.call_gemma(prompt, num_predict=300, temperature=0.5,
                                             timeout=180).strip()[:600]
        except Exception:
            d = job.get("delta_usdt") or 0.0
            text = (f"結果が出たよ。いつもの戦略と比べて {d:+.1f} USDT でした。"
                    "詳しくはカードの数字を見てね。")
        meta["explained"] = True
        meta["kurage_says"] = text
        sess["history"].append({"role": "kurage", "text": text})
    if meta:
        out["kurage_says"] = meta.get("kurage_says")
    if job["status"] == "failed" and meta and not meta.get("explained"):
        meta["explained"] = True
        meta["kurage_says"] = "ごめんなさい、検証が途中で失敗しちゃいました。もう一回試すか、少し時間を置いてみてください。"
        out["kurage_says"] = meta["kurage_says"]
    return out


app.mount("/", StaticFiles(directory=os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "static"), html=True), name="static")
