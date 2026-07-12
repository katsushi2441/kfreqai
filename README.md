# kfreqai

Kurageプロジェクトの暗号資産AI自動取引bot。[FreqAI](https://www.freqtrade.io/en/stable/freqai/)(LightGBM)による2時間先予測を軸に、独自のリスク管理層(過熱フィルター・銘柄別出禁・ボラ連動サイジング・地合い連動の同時保有枠)を重ねている。**現在はdry-run(紙上取引)のみで、実資金は動かしていない。**

進捗・事故対応・検証結果は[取引ブログ](https://kurage.exbridge.jp/blog/)で公開している。ダッシュボードは`kfreqai.php`([kurage_web](https://github.com/katsushi2441/kurage_web)側)から参照。

## 構成

freqtrade本体は`vendor/freqtrade`にgit submoduleとして置いている(参照用)。**実行時は`docker-compose.yml`が`freqtradeorg/freqtrade:stable_freqai`の公式配布イメージを使う**ため、`vendor/freqtrade`のソースはコンテナには一切マウントされない。読み書きするのは`user_data/`だけ。

```
user_data/
  strategies/
    kurage_freqai_strategy.py   # ライブ戦略の本体(FreqAI特徴量・エントリー/エグジット・保護ルール)
    kfreqai_variant_*.py        # 検証用の派生戦略(採用/却下の記録はhypothesis_ledger.jsonlへ)
  lab/
    hypothesis_ledger.jsonl     # 検証した仮説の台帳(採否と根拠を記録)
    trade_journal.jsonl         # 反省会ループの取引ふりかえり
  config.json                  # ライブ設定(gitignore対象。秘密情報を含む)
  config_experiment*.json      # バックテスト用設定(30日/6ヶ月ホールドアウト)

kurage-advisory/                # LLM反省会・地合い判定・ニュース監視ループ(systemdタイマー駆動)
kurage-growth/                  # 銘柄拡張・出来高調査スクリプト
kurage-scripts/                 # バックテスト・デプロイ補助スクリプト
kurage-systemd/                 # 上記の systemd unit/timer 定義
blog-bludit/                    # 取引ブログ(Bludit)投稿まわり
landing/                        # kfreqai.exbridge.jp 静的プロモページ
vendor/freqtrade/               # upstream freqtrade(submodule, 参照専用)
```

## 起動

```bash
docker compose up -d          # ライブ稼働(dry-run)
docker compose logs -f freqtrade
```

`docker-compose.yml`内の`--strategy` / `--freqaimodel`が実際に使われる戦略とモデルを決める。切り替えたらコンテナの再作成(`docker compose up -d`、reload_configでは拾わない)が必要。

## バックテスト

いつもの検証手順は「30日・160銘柄」+「6ヶ月ホールドアウト・17銘柄」の2窓で、両方を悪化させない場合のみ採用する。

```bash
docker compose run --rm freqtrade backtesting \
  --config user_data/config_experiment160.json \
  --strategy <戦略名> --freqaimodel <モデル名> \
  --enable-protections --timerange 20260610-20260709
```

検証結果は`user_data/lab/hypothesis_ledger.jsonl`に追記し、採用したものだけライブ戦略へ反映する。

## upstream freqtradeを参照する

```bash
git submodule update --init vendor/freqtrade
```

コード検索・API仕様の確認・freqtradeのソースを読みたいときはここを見る。stableブランチを追従しているだけで、kfreqai側の改変は一切入っていない。
