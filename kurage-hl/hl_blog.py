#!/usr/bin/env python3
"""kfreqaihl 日次総括ブログ(各テナント1日1記事・JST「1日の終わり」に投稿)。

kfreqai/kfxai と同じ Kurage ブログ基盤(Bludit)へ、kfreqaihl の各テナント
(xb_bittensor 本人 + アンバサダー)の当日取引を総括した記事を投稿する。

  - 対象: list_active_tenants()(メインウォレット登録済み)の全テナント
  - 集計: crypto本番(Hyperliquid perp testnet) + 現物ペーパー + FXペーパー の
          当日約定(fills, time_ms が JST 当日のもの)を統合
  - 記事: 数値はコード側で確定し、Gemma 4 は文章化のみ(数値の創作を禁止)
  - 投稿: kurage_blog.post_to_bludit(category="kfreqaihl", 免責フッター付き)
  - 告知: AIxSNS(author=kurage)
  - 分散: アンバサダー最大50人でも同一ブログ基盤に集中しないよう、テナントごとに
          HL_BLOG_INTERVAL 秒(既定90s)あけて順次投稿する。timer は 23:00 起動。
  - 新規参加: created_at が当日のテナントは、総括の前に「参加告知」記事を1本出す。

Hard rule: kfreqaihl は testnet + ペーパー。実資金は一切動いていない。免責は
DISCLOSURE_FOOTER でコード側が必ず付与し、LLM 生成には依存しない。

  --dry-run             投稿せず生成本文を標準出力に出すだけ(品質確認用)
  --only <username>     指定テナント1人だけ処理(テスト用)
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))               # kurage-hl
sys.path.insert(0, "/home/kojima/work/kfreqai/kurage-advisory")               # kurage_blog

import kurage_blog  # noqa: E402  (Kurageブログ(Bludit)の持ち主。一方向で使う)
import hl_connector  # noqa: E402
import hl_paper_fx  # noqa: E402
import hl_paper_spot  # noqa: E402
import tenant_store  # noqa: E402

JST = datetime.timezone(datetime.timedelta(hours=9))
CATEGORY = "kfreqaihl"
TAGS = "AI自動取引,Hyperliquid,暗号資産,FX,kfreqaihl"
OLLAMA_URL = os.environ.get("HL_BLOG_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("HL_BLOG_OLLAMA_MODEL", "gemma4:12b-it-qat").strip()
AIXSNS_API = os.environ.get("AIXSNS_API", "https://aixec.exbridge.jp/api.php?path=posts")
POST_INTERVAL_SEC = int(os.environ.get("HL_BLOG_INTERVAL", "90"))
DASHBOARD_URL = "https://kurage.exbridge.jp/kfreqaihl.php"

DISCLOSURE_FOOTER = (
    "\n\n---\n\n"
    "**注記**: kfreqaihl は Hyperliquid の **testnet**(テスト網)で稼働する自動取引の"
    "シミュレーションで、**実際の資金は一切動いていません**。現物・FXはペーパートレード"
    "(実勢価格に対する仮想取引)です。本記事の残高・損益・取引はすべてシミュレーション上の"
    "数値です。暗号資産・FXはレバレッジにより大きな損失が生じうる高リスク資産です。"
    "本記事は投資助言ではありません。"
    f"[kfreqaihl ダッシュボード]({DASHBOARD_URL})"
)


# ---------------------------------------------------------------------------
# LLM (Gemma 4, ローカル) — 文章化のみ。数値はプロンプトで固定し創作させない。
# ---------------------------------------------------------------------------
def call_gemma(prompt: str, num_predict: int = 1400, temperature: float = 0.5,
               timeout: int = 300) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "think": False,  # gemma4は思考型: 無効化しないと隠れ推論でnum_predictを食い潰し空応答になる
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    resp = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=timeout)
    resp.raise_for_status()
    text = resp.json().get("response") or ""
    if not text.strip():
        raise RuntimeError("gemma4 returned empty response")
    return text.strip()


# ---------------------------------------------------------------------------
# データ収集(当日 fills の統合集計)
# ---------------------------------------------------------------------------
RUN_DATE = datetime.datetime.now(JST).date()  # 起動時に固定(全テナント処理中の日付跨ぎで集計がズレないように)


def _today():
    return RUN_DATE


def _fills_today(fills):
    """time_ms が JST 当日の約定だけ残す。"""
    today = _today()
    out = []
    for f in fills or []:
        ts = int(f.get("time_ms") or 0)
        if ts and datetime.datetime.fromtimestamp(ts / 1000, JST).date() == today:
            out.append(f)
    return out


def collect_today(username):
    """crypto本番(perp testnet) + 現物ペーパー + FXペーパー の当日約定を集める。"""
    data = {"crypto": [], "spot": [], "fx": [], "positions": []}
    tenant = tenant_store.get_or_create(username, hl_connector.generate_agent_wallet)
    if tenant.get("main_wallet_address"):
        try:
            d = hl_connector.get_dashboard(tenant["main_wallet_address"])
            data["crypto"] = _fills_today(d.get("fills"))
            data["positions"] = d.get("positions") or []
        except Exception as exc:
            print("[hl_blog] crypto dashboard failed %s: %s" % (username, str(exc)[:100]), flush=True)
    try:
        s = hl_paper_spot.paper_dashboard(username)
        if s.get("enabled"):
            data["spot"] = _fills_today(s.get("fills"))
    except Exception as exc:
        print("[hl_blog] spot dashboard failed %s: %s" % (username, str(exc)[:100]), flush=True)
    try:
        fx = hl_paper_fx.paper_dashboard(username)
        if fx.get("enabled"):
            data["fx"] = _fills_today(fx.get("fills"))
    except Exception as exc:
        print("[hl_blog] fx dashboard failed %s: %s" % (username, str(exc)[:100]), flush=True)
    return data


def _leg_summary(fills):
    """1レグ(crypto/spot/fx)の当日集計。決済(closed_pnl!=0)を成績として数える。"""
    closes = [f for f in fills if abs(float(f.get("closed_pnl_usd") or 0)) > 1e-9]
    pnl = sum(float(f.get("closed_pnl_usd") or 0) for f in closes)
    wins = sum(1 for f in closes if float(f.get("closed_pnl_usd") or 0) > 0)
    losses = sum(1 for f in closes if float(f.get("closed_pnl_usd") or 0) < 0)
    opens = [f for f in fills if f not in closes]
    coins = sorted({(f.get("coin") or f.get("pair") or "?") for f in fills})
    return {"trades": len(fills), "closes": len(closes), "pnl": round(pnl, 2),
            "wins": wins, "losses": losses, "opens": len(opens), "coins": coins}


def summarize(data):
    legs = {k: _leg_summary(data.get(k, [])) for k in ("crypto", "spot", "fx")}
    total_trades = sum(legs[k]["trades"] for k in legs)
    total_pnl = round(sum(legs[k]["pnl"] for k in legs), 2)
    total_wins = sum(legs[k]["wins"] for k in legs)
    total_losses = sum(legs[k]["losses"] for k in legs)
    return {"legs": legs, "total_trades": total_trades, "total_pnl": total_pnl,
            "total_wins": total_wins, "total_losses": total_losses,
            "open_positions": len(data.get("positions") or [])}


# ---------------------------------------------------------------------------
# 記事生成
# ---------------------------------------------------------------------------
LEG_JA = {"crypto": "暗号資産(Hyperliquid perp・testnet)", "spot": "現物(ペーパー)",
          "fx": "FX・商品・指数(ペーパー)"}


def _facts_block(display, s):
    lines = [f"【{display} の本日({_today().isoformat()})の取引実績・確定値】",
             f"- 合計約定件数: {s['total_trades']}件",
             f"- 合計確定損益: {s['total_pnl']:+.2f} USD",
             f"- 勝ち/負け(決済ベース): {s['total_wins']}勝 {s['total_losses']}敗",
             f"- 現在の保有ポジション数: {s['open_positions']}件"]
    for k in ("crypto", "spot", "fx"):
        leg = s["legs"][k]
        if leg["trades"] == 0:
            continue
        coins = "、".join(leg["coins"][:8]) if leg["coins"] else "-"
        lines.append(
            f"- {LEG_JA[k]}: 約定{leg['trades']}件(新規{leg['opens']}/決済{leg['closes']})、"
            f"確定{leg['pnl']:+.2f} USD、{leg['wins']}勝{leg['losses']}敗、対象: {coins}")
    return "\n".join(lines)


def build_article(display, data):
    """(title, slug, body) を返す。取引ゼロの日は None(投稿しない)。"""
    s = summarize(data)
    if s["total_trades"] == 0:
        return None
    date_ja = datetime.datetime.now(JST).strftime("%Y年%-m月%-d日")
    facts = _facts_block(display, s)
    pnl_word = "プラス" if s["total_pnl"] > 0 else ("マイナス" if s["total_pnl"] < 0 else "収支トントン")
    prompt = (
        "あなたはKurageというAI自動取引システムのキャラクターです。以下の『確定値』だけを使って、"
        f"{display} の {date_ja} の1日の取引を振り返る、やわらかい日本語のブログ記事を書いてください。\n\n"
        "厳守事項:\n"
        "1. 数字は下の確定値だけを使う。確定値に無い数字・銘柄・比率を創作しない。\n"
        "2. 暗号資産・現物・FXを横断した『1日の総括』にする。\n"
        "3. 断定的な予測や投資助言はしない。淡々と事実を語る。\n"
        "4. 見出しは付けてよいが、免責やリンクは書かない(後で自動付与する)。\n"
        f"5. 全体で400〜700字程度。損益は全体として{pnl_word}だった点に触れる。\n\n"
        f"{facts}\n\n"
        "本文(Markdown):"
    )
    try:
        body = call_gemma(prompt)
    except Exception as exc:
        print("[hl_blog] gemma failed, fallback: %s" % str(exc)[:100], flush=True)
        body = _fallback_body(display, date_ja, s)
    # 生成本文の後ろに、必ず確定値サマリ表を機械的に付ける(数値の担保)
    body += "\n\n" + _facts_table(s)
    title = f"{display} の{date_ja}の取引総括 — kfreqaihl"
    slug = f"kfreqaihl-daily-{_sanitize(display)}"
    return title, slug, body


def _fallback_body(display, date_ja, s):
    w = "プラス" if s["total_pnl"] > 0 else ("マイナス" if s["total_pnl"] < 0 else "トントン")
    return (f"{display} の {date_ja} の取引を振り返ります。本日は合計 {s['total_trades']} 件の約定があり、"
            f"確定損益は全体で {s['total_pnl']:+.2f} USD（{w}）でした。"
            f"決済ベースの成績は {s['total_wins']} 勝 {s['total_losses']} 敗、"
            f"現在 {s['open_positions']} 件のポジションを保有しています。")


def _facts_table(s):
    rows = ["| レグ | 約定 | 決済 | 確定損益(USD) | 勝敗 |",
            "|---|---:|---:|---:|---|"]
    for k in ("crypto", "spot", "fx"):
        leg = s["legs"][k]
        if leg["trades"] == 0:
            continue
        rows.append(f"| {LEG_JA[k]} | {leg['trades']} | {leg['closes']} | "
                    f"{leg['pnl']:+.2f} | {leg['wins']}勝{leg['losses']}敗 |")
    rows.append(f"| **合計** | **{s['total_trades']}** | — | **{s['total_pnl']:+.2f}** | "
                f"**{s['total_wins']}勝{s['total_losses']}敗** |")
    return "\n".join(rows)


def _sanitize(name):
    return "".join(c if (c.isalnum() or c in "-_") else "-" for c in str(name)).strip("-").lower() or "user"


def build_join_article(display):
    """アンバサダー参加告知記事(created_at当日)。"""
    date_ja = datetime.datetime.now(JST).strftime("%Y年%-m月%-d日")
    title = f"{display} さんが kfreqaihl のアンバサダーに参加しました"
    slug = f"kfreqaihl-join-{_sanitize(display)}"
    body = (f"{date_ja}、**{display}** さんが kfreqaihl(Kurage FreqAI Trade for Hyperliquid)の"
            "アンバサダーとして参加しました。\n\n"
            "kfreqaihl は、暗号資産(Hyperliquid perp)・現物・FX/商品/指数を、AIの判断ゲート付きで"
            "自動売買するマルチテナントのシステムです(testnet・ペーパーによる検証運用)。"
            f"これから {display} さんの日々の取引も、1日の終わりにこのブログで総括していきます。")
    return title, slug, body


# ---------------------------------------------------------------------------
# 投稿・告知
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 冪等化: 「各ユーザ1日1個だけ」。当日・username・種別ごとに投稿済みを記録し、
# timerの再実行や手動+自動の二重起動でも二重投稿しないようにする。
# ---------------------------------------------------------------------------
LEDGER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "user_data", "hl_blog_posted.json")


def _load_ledger():
    try:
        with open(LEDGER_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _already_posted(username, kind):
    led = _load_ledger()
    return kind in (led.get(_today().isoformat(), {}).get(username, []))


def _mark_posted(username, kind):
    led = _load_ledger()
    day = led.setdefault(_today().isoformat(), {})
    kinds = day.setdefault(username, [])
    if kind not in kinds:
        kinds.append(kind)
    # 直近14日分だけ保持(肥大化防止)
    cutoff = (_today() - datetime.timedelta(days=14)).isoformat()
    led = {d: v for d, v in led.items() if d >= cutoff}
    tmp = LEDGER_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(led, f, ensure_ascii=False)
    os.replace(tmp, LEDGER_PATH)


def announce_aixsns(display, title, permalink):
    content = (f"kfreqaihl を更新しました。{display} さんの本日の取引総括です。\n\n"
               f"{title}\n\n記事: {permalink}\nダッシュボード: {DASHBOARD_URL}")
    try:
        requests.post(AIXSNS_API, json={"author": "kurage", "content": content}, timeout=15)
    except Exception as exc:
        print("[hl_blog] aixsns announce failed: %s" % str(exc)[:100], flush=True)


def _post(title, slug, body, dry_run):
    if dry_run:
        print("=" * 70)
        print("TITLE:", title)
        print("SLUG :", slug)
        print(body + DISCLOSURE_FOOTER)
        return f"(dry-run)/{slug}"
    _, permalink = kurage_blog.post_to_bludit(title, slug, body, tags=TAGS,
                                              category=CATEGORY, footer=DISCLOSURE_FOOTER)
    return permalink


def _display_of(tenant):
    return tenant.get("username") or "user"


def process_tenant(tenant, dry_run=False):
    username = tenant["username"]
    display = _display_of(tenant)
    posted = []
    # 参加当日は参加告知を先に(当日1回だけ)
    created = str(tenant.get("created_at") or "")
    if created[:10] == _today().isoformat() and not (not dry_run and _already_posted(username, "join")):
        t, sl, b = build_join_article(display)
        permalink = _post(t, sl, b, dry_run)
        if not dry_run:
            announce_aixsns(display, t, permalink)
            _mark_posted(username, "join")
        posted.append(("join", permalink))
    # 当日総括(当日1回だけ)
    if not dry_run and _already_posted(username, "summary"):
        print("[hl_blog] %s: 本日は総括投稿済み、スキップ" % username, flush=True)
        return posted
    data = collect_today(username)
    art = build_article(display, data)
    if art:
        t, sl, b = art
        permalink = _post(t, sl, b, dry_run)
        if not dry_run:
            announce_aixsns(display, t, permalink)
            _mark_posted(username, "summary")
        posted.append(("summary", permalink))
    else:
        print("[hl_blog] %s: 当日取引なし、総括スキップ" % username, flush=True)
    return posted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    tenants = tenant_store.list_active_tenants()
    if args.only:
        tenants = [t for t in tenants if t["username"] == args.only]
    print("[hl_blog] 対象テナント %d 件" % len(tenants), flush=True)
    for i, tenant in enumerate(tenants):
        try:
            posted = process_tenant(tenant, dry_run=args.dry_run)
            print("[hl_blog] %s -> %s" % (tenant["username"], posted or "投稿なし"), flush=True)
        except Exception as exc:
            print("[hl_blog] %s failed: %s" % (tenant["username"], str(exc)[:200]), flush=True)
        # テナント間は分散(同一ブログ基盤への集中回避)。最後の1人の後は待たない。
        if not args.dry_run and i < len(tenants) - 1:
            time.sleep(POST_INTERVAL_SEC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
