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

## 足と評価周期（2026-08-03 変更）

`.env` に以下を設定している。

```
HL_DEFAULT_INTERVAL=15m
HL_CYCLE_SECONDS=900
```

変更前はコード既定の 1h 足 / 3600 秒周期だった。kfreqai は 5m 足なので判断機会が
1/12 しかなく、実測で **kfreqai 20.7 トレード/日 に対し kfreqaihl 1.8 トレード/日**
だった。取引数が少ない原因は資金ではなくこれ。

`hl_backtest.run_backtest(days=14, coin_cap=20)` での比較:

| 足 | 取引/日 | 勝率 | 収益 | 最大DD | 平均損益/取引 |
|---|---:|---:|---:|---:|---:|
| 1h | 3.59 | 24.1% | -25.43% | 24.77% | -4.59 |
| 15m | 5.96 | 37.6% | -26.17% | 26.11% | -3.05 |
| 5m | 10.79 | 38.2% | -27.61% | 27.68% | -1.82 |

短いほど勝率と1取引あたり損失は改善し、総収益と最大DDの差は誤差範囲。
API 負荷とのバランスで 15m を採用した（5m は負荷3倍）。

なおこの検証期間は3つの足すべてが負けており、**足の短縮は「取引機会を増やす」
変更であって「勝てるようにする」変更ではない**。収益改善は別途の課題。

## 市場ごとの稼働状況

| 市場 | 状態 |
|---|---|
| FX (`hl_paper_fx`) | 稼働中。engine ループから毎周期呼ばれる |
| 現物 (`hl_paper_spot`) | ループには入っているが約定が少ない。ロング限定 + freqai の上昇予測フィルタで条件が厳しいため |
| MEXC 先物 (`hl_paper_mexcf`) | **意図的に撤去済み**（2026-07-27）。freqtrade の先物モードで動くため kfreqai 側 `kfreqai-futures-short` へ一本化した。kfreqaihl は Hyperliquid 専用という製品境界を保つため、ここへ戻さないこと。DB に残る古い `mexcf` の行はその名残で、画面には出ない |

## 価格データ

`HL_USE_TESTNET=1`（testnet）で運用しているが、testnet と mainnet の価格は近い
（2026-08-03 実測: BTC testnet 63,330 / mainnet 63,215、乖離 0.18%）。
ただし FX/商品/指数の builder-dex 銘柄（`xyz:EUR` 等）は testnet に履歴が無く、
`hl_loop.fetch_candles` が mainnet を直叩きする実装になっている。
