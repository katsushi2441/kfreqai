# kfreqai judgment API

kfreqaiのLLM判断機能(地合い判定・リスク指令・ニュース分類・トレード反省会・
仮説研究・記事執筆)をRESTで公開するサービス。LLM環境を持たないユーザーが、
kfreqai風の自作botからHTTPだけで判断機能を使えるようにする。

- ローカル: `127.0.0.1:18321`、無課金・無認証。
- 将来: この前段にx402課金ゲートウェイ(url2ai llm-gatewayと同構成)を置いて
  有料公開する。ゲートウェイは必ず `engine: "gemma"` を強制すること
  (Claude CLI(個人サブスク)を有料経路に乗せないための規約対策)。

## 起動

```bash
# 手動
cd kurage-advisory && python3 -m uvicorn judgment_api:app --host 127.0.0.1 --port 18321

# systemd
cp kurage-systemd/kfreqai-judgment-api.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now kfreqai-judgment-api
```

## エンジン切替

全エンドポイント共通(ボディの `engine`、またはヘッダ `X-Judgment-Engine`):

| engine | 動作 | 用途 |
|---|---|---|
| `auto` (既定) | judgment_backendそのまま(claude → codex → gemma4 の3段フォールバック) | ローカル利用 |
| `gemma` | gemma4(Ollama)のみ | x402課金経路(必須) |

LLM呼び出しはプロセス内で直列化される(GPU1本)。重い呼び出しが重なると
待たされるので、クライアント側タイムアウトは300秒以上を推奨。

## エンドポイント

### GET /v1/health
バックエンド名・gemmaエンジン可否・Ollama到達性。

### GET /v1/meta
ニュース分類カテゴリ・ブロック閾値・反省会カテゴリ・仮説DSL(features/ops/params)・
OHLCV形式。外部クライアントはまずこれを読む。

OHLCV形式(全エンドポイント共通): `pair -> [[timestamp_ms, open, high, low, close, volume], ...]`
1時間足で30本以上推奨(出来高倍率・値幅倍率の計算に30本使う)。

### POST /v1/judge/regime — 地合い判定(毎時向け)
```bash
curl -s localhost:18321/v1/judge/regime -H 'Content-Type: application/json' -d '{
  "ohlcv_by_pair": {"BTC/USDT": [[1752537600000, 100, 101, 99, 100.5, 1200], ...]}
}'
# -> {"regime": "bullish|bearish|neutral", "note": "...", "model": "gemma4:12b-it-qat"}
```

### POST /v1/judge/directive — リスク指令(1日数回向け)
```bash
curl -s localhost:18321/v1/judge/directive -H 'Content-Type: application/json' -d '{
  "ohlcv_by_pair": {...},
  "history": [{"type": "regime", "updated_at_iso": "...", "value": "neutral", "note": ""}],
  "engine": "gemma"
}'
# -> {"directive": "risk_on|risk_off|neutral", "note": "...", "model": "..."}
```

### POST /v1/judge/news — ニュース分類
```bash
curl -s localhost:18321/v1/judge/news -H 'Content-Type: application/json' -d '{
  "article": {"title": "Exchange X delists ABC", "summary": "..."},
  "symbols": ["ABC"]
}'
# -> {"signals": [{"symbol": "ABC", "sentiment": "negative",
#                  "event_type": "delisting", "confidence": 0.9}],
#     "negative_events": [...], "block_confidence_threshold": 0.6}
```
`sentiment=negative` かつ `event_type ∈ negative_events` かつ
`confidence >= block_confidence_threshold` ならエントリーブロック相当。

### POST /v1/judge/postmortem — トレード反省会
```bash
curl -s localhost:18321/v1/judge/postmortem -H 'Content-Type: application/json' -d '{
  "ctx": {"pair": "ABC/USDT", "open_date": "2026-07-15T02:00", "held_hours": 1.5,
          "pre_entry_chg_4h_pct": 8.2, "mae_pct": -5.1, "mfe_pct": 0.4,
          "result_pct": -5.0, "exit_reason": "stop_loss"}
}'
# -> {"category": "pump_chase", "verdict": "bad_entry", "lesson": "..."}
```
ctxの各フィールドの意味は trade_postmortem.py の build_postmortem_context を参照。
自前で計算できない項目はnullでよい(LLMは与えられた事実だけで解釈する)。

### POST /v1/research/hypotheses — 仮説提案
実績ドシエ(勝率・敗因分布・検証済み仮説リスト等の自由形式dict)から、
バックテストで検証可能な仮説JSON(最大2個)を提案する。
```bash
curl -s localhost:18321/v1/research/hypotheses -H 'Content-Type: application/json' -d '{
  "dossier": {"winrate": 0.53, "worst_categories": ["pump_chase"], "already_tested": []},
  "engine": "gemma"
}'
```

### POST /v1/research/variant-code — 仮説→戦略コード(LLM不使用・決定的)
仮説JSONをfreqtradeの検証用variant戦略クラスに変換する。
```bash
curl -s localhost:18321/v1/research/variant-code -H 'Content-Type: application/json' -d '{
  "hypothesis": {"name": "skip_pump", "kind": "entry_filter",
                 "conditions": [{"feature": "chg_4h", "op": ">", "value": 0.1}]}
}'
# -> {"code": "...python...", "class_name": "KfreqaiLabHypo", ...}
```

### POST /v1/backtest — 仮説をバックテストで検証(非同期ジョブ)
バイブコーディングの検証部分。仮説DSLだけを受け付け(任意コードは実行しない)、
主要13銘柄(BTC/ETH/SOL/XRP/ADA/DOGE/LINK/LTC/DOT/AVAX/UNI/ATOM/NEAR)で
バックテストし、ベースライン戦略との損益差を返す。
```bash
curl -s localhost:18321/v1/backtest -H 'Content-Type: application/json' -d '{
  "hypothesis": {"name": "skip_overheat", "kind": "entry_filter",
                 "conditions": [{"feature": "chg_4h", "op": ">", "value": 0.08}]},
  "timerange": "20260613-20260713"
}'
# -> {"job_id": "...", "status": "queued", ...}

curl -s localhost:18321/v1/backtest/<job_id>
# -> {"status": "queued|running|done|failed",
#     "baseline": {"trades": N, "profit_abs": X, "profit_pct": Y},
#     "result":   {...同形式...},
#     "delta_usdt": +123.4, "verdict": "candidate|worse", "error": null}

curl -s localhost:18321/v1/backtest       # 直近ジョブ一覧
```
- timerange省略時は「直近の月曜で終わる30日窓」。最大60日・未来不可。
- ジョブは直列実行。その timerange の初回はベースライン学習込みで数十分、
  以降はFreqAI予測キャッシュを再利用するので大幅に短い。
- %と小数の取り違え(chg_4h > 5 等)は自動補正される(rationaleに注記が付く)。
- advisory_state/news_signalsは空JSONをシャドウマウントするため、
  「今の」地合い判定が過去窓に漏れない(結果は決定的)。
- 429=キュー満杯(10件)。failedの`error`にfreqtrade側の末尾ログが入る。

### POST /v1/write/status-article, /v1/write/blog-article, /v1/write/satellite-article
運用ブログ記事の自動執筆(入出力はソース参照)。satelliteは品質ゲートに
落ちると502を返すのでリトライすること。

## エラー

- 422: 入力スキーマ違反(メッセージに理由)
- 502: LLM出力がパース不能/品質ゲート落ち(リトライ推奨)
- 503: そのデプロイでgemmaエンジンが使えない(judgment_logic非搭載)
