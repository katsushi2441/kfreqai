# kfreqaihl 運用メモ

`.env` は gitignore 対象で経緯が残らないため、運用上の判断はここに書く。

## systemd は **system unit**（user unit ではない）

kfreqaihl の2プロセスは `/etc/systemd/system/` に **system unit** として導入済み。

| unit | 役割 |
|---|---|
| `kfreqai-kurage-hl.service` | FastAPI (`hl_api:app`, 0.0.0.0:18339) |
| `kfreqai-kurage-hl-engine.service` | 取引エンジン (`hl_engine.py`) |

確認は `systemctl status`（`--user` ではない）。

```bash
systemctl status kfreqai-kurage-hl-engine.service
journalctl -u kfreqai-kurage-hl-engine.service -f
```

**`systemctl --user` で見ると「存在しない」ように見えるので注意。** ワークスペースの
他サービスは user unit が原則だが、ここだけは system unit で動いている。
user 側に同名 unit を作ると **engine が二重稼働して二重発注になる**（2026-08-03 に実際に発生させた）。

`kurage-systemd/*.service` の `User=kojima` は system unit だから必要な行で、
消してはいけない（user unit だと `status=216/GROUP` で起動しない、という別の話と混同しない）。

プロセスを手で kill しても `Restart=always` で復帰する。復帰したものを「孤児」と
誤認して kill を繰り返すと `NRestarts` だけが膨らむ。停止したいときは
`systemctl stop`（要 sudo）を使う。

## データの置き場所（**kfreqai と kfreqaihl で別**・最重要）

| 対象 | 実データ |
|---|---|
| kfreqai（8コンテナ全部） | **`/mnt/data/kfreqai/user_data/`**（別ディスク sda1）。`docker inspect` で `-> /freqtrade/user_data` にbind mount |
| kfreqaihl（engine/API） | **`/home/kojima/work/kfreqai/user_data/hl_tenants.sqlite`**（repo配下）。`tenant_store.DB_PATH` |

**両方に `user_data/` があり、中身は別物。** `/mnt/data` 側にも `hl_tenants.sqlite` が
残っているが 2026-08-02 の移行時のコピーで**更新されていない**。

repo配下の `tradesv3.sqlite` を読んで kfreqai の取引状況を判断すると必ず間違う
（2026-08-03 に実際にやり、「kfreqai は24時間エントリーしていない」と誤報告した。
実際は当日15:05にエントリー済み）。kfreqai を見るときは `/mnt/data`、
kfreqaihl を見るときは repo配下、と毎回確認する。

## 足と評価周期（2026-08-03 変更）

`.env` に以下を設定している。

```
HL_DEFAULT_INTERVAL=5m
HL_CYCLE_SECONDS=300
HL_ENTRY_SOURCE=both
```

変更前はコード既定の 1h 足 / 3600 秒周期だった。kfreqai が 5m 足なのに合わせ、
判断機会を揃えるために 5m / 300 秒にした。

`hl_backtest.run_backtest(days=14, coin_cap=20)` での比較:

| 足 | 取引/日 | 勝率 | 収益 | 最大DD | 平均損益/取引 |
|---|---:|---:|---:|---:|---:|
| 1h | 3.59 | 24.1% | -25.43% | 24.77% | -4.59 |
| 15m | 5.96 | 37.6% | -26.17% | 26.11% | -3.05 |
| 5m | 10.79 | 38.2% | -27.61% | 27.68% | -1.82 |

短いほど勝率と1取引あたり損失は改善し、総収益と最大DDの差は誤差範囲（DDは実行ごとに
±2pt 程度ぶれる）。負荷は 1h の12倍だが、まず取引機会を揃えて様子を見る方針。

なおこの検証期間は3つの足すべてが負けており、**足の短縮は「取引機会を増やす」
変更であって「勝てるようにする」変更ではない**。収益改善は別途の課題。

### 取引数の比較で誤らないための実測値（2026-08-03 15:46 JST）

当初この文書には「kfreqai 20.7 トレード/日 に対し kfreqaihl 1.8 トレード/日」と
書いていたが、**これは repo 配下の古い sqlite を読んだ誤り**。正しい実測は以下。

| | 24h エントリー | 最終 |
|---|---:|---|
| kfreqai 本番bot 単体（`tradesv3.sqlite`） | 4 | 08/03 15:05 JST |
| kfreqai `*-short` 系 4bot | 各 18 | 08/03 15:43 JST |
| kfreqai 8bot 合計 | 78 | |
| kfreqaihl（fx 5 + spot 1、うち新規buy 4） | 4〜6 | 08/03 15:40 JST |

**bot 単体で見ると kfreqaihl は kfreqai 本番と同等以上。** 「kfreqai の方が圧倒的に
多い」という印象の実体は 8bot の合算、特に取引頻度の高い `*-short` 系の寄与。
比較するときは必ず bot 単位で揃える。

## 市場ごとの稼働状況

| 市場 | 状態 |
|---|---|
| FX (`hl_paper_fx`) | 稼働中。engine ループから毎周期呼ばれる |
| 現物 (`hl_paper_spot`) | 稼働中。ロング限定 + freqai の上昇予測フィルタで条件は厳しい。2026-08-03 まで決済管理が毎周期例外で落ちていた（下記） |
| MEXC 先物 (`hl_paper_mexcf`) | **意図的に撤去済み**（2026-07-27）。freqtrade の先物モードで動くため kfreqai 側 `kfreqai-futures-short` へ一本化した。kfreqaihl は Hyperliquid 専用という製品境界を保つため、ここへ戻さないこと。DB に残る古い `mexcf` の行はその名残で、画面には出ない |

## エントリー根拠の切替（`HL_ENTRY_SOURCE`、2026-08-03 追加）

kfreqaihl のエントリー判断は `hl_engine.decide_entry()` に集約し、`.env` の
`HL_ENTRY_SOURCE` で源を切り替える。

| 値 | 挙動 |
|---|---|
| `core` | 従来どおり `strategy_core`（EMA 等）だけで判断 |
| `freqai` | kfreqai の FreqAI 予測をロングのシグナル源にする。予測が無ければ見送り |
| `both`（現行） | まず FreqAI 予測、出なければ `strategy_core` にフォールバック |

**ゲートとシグナル源で fail 方向が逆**なので混同しないこと（`hl_brain_client.py`）。

- `freqai_long_ok()` = **ゲート**。予測が取れないときは **fail-open**（Trueを返して通す）
- `freqai_long_signal()` = **シグナル源**。予測が取れないときは **fail-closed**（Noneでエントリーしない）

fail-open のまま源に使うと、予測が落ちている間じゅう全銘柄でエントリーしてしまう。

予測は kfreqai の judgment API（:18321）経由で読む。参照先は system unit の
`Environment=KFREQAI_FREQAI_PRED_PATH=/mnt/data/kfreqai/user_data/freqai_predictions.json`。
**このパスが repo 配下を指していると、停止した古いファイルを読み続けて
`available: false` になる**（2026-08-03 に発生・修正済み）。

builder-dex 銘柄（`xyz:EUR` 等）は kfreqai に予測が無いので `decide_entry` 側で
除外し、常に `strategy_core` で判断する。

## 過去のバグ（同じ誤診を繰り返さないため）

### 現物の決済管理が毎周期落ちていた（2026-08-02〜08-03、修正済み）

`hl_paper_spot._manage` が `if strategy_core.exit_long_cond(df, p):` と書いており、
Series をそのまま真偽判定して `ValueError: The truth value of a Series is ambiguous`
で 24 時間に 42 回落ちていた。**現物のストップ/トレール/EMA反転が一度も効いていない
状態**だった。`.iloc[-1]` で最終足を取り出して解決（`hl_paper_fx` / `hl_engine` は
元から正しく書けている）。

「現物の約定が少ない」の一部はこれが原因で、戦略の厳しさだけではなかった。

## 価格データ

`HL_USE_TESTNET=1`（testnet）で運用しているが、testnet と mainnet の価格は近い
（2026-08-03 実測: BTC testnet 63,330 / mainnet 63,215、乖離 0.18%）。
ただし FX/商品/指数の builder-dex 銘柄（`xyz:EUR` 等）は testnet に履歴が無く、
`hl_loop.fetch_candles` が mainnet を直叩きする実装になっている。
