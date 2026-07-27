"""KurageFuturesShortStrategy — MEXC先物ショート専用(freqtrade futuresモード・dry-run)。

経緯(2026-07-27): 「MEXC先物APIは発注不可」という古い誤情報を根拠に自作ペーパー
エンジン(kurage-hl/hl_paper_mexcf)を作ってしまったが、実際はAPIも先物権限も有効で、
freqtradeの先物モードがそのまま使えることを実測確認。よって自作エンジンを廃止し、
本戦略でfreqtradeに一本化する(kfreqai.php のロング/ショート1画面統合の土台)。

シグナルは旧ペーパーエンジン(strategy_core標準型)の移植:
- エントリー(ショート): EMA12がEMA26を下抜け(デッドクロス)
- 手仕舞い: EMA12がEMA26を上抜け(ゴールデンクロス) = exit_signal相当
- stoploss -6% = 標準型 stoploss_pct
- トレーリング: +4%到達後、+3%を割ったら利確
  (旧peak_trail_trigger 4% / giveback 25% の近似。freqtradeのトレーリングは
   「ピークからの相対%」ではなく「絶対オフセット」なので厳密一致はしない)
"""
from freqtrade.strategy import IStrategy
import talib.abstract as ta

# MEXC先物許可パッチはプロセス起動時(sitecustomize)に適用済み。
# 詳細な経緯: user_data/freqtrade_patches/mexc_futures.py


class KurageFuturesShortStrategy(IStrategy):
    INTERFACE_VERSION = 3

    can_short = True
    timeframe = "1h"
    process_only_new_candles = True
    startup_candle_count = 40

    stoploss = -0.06
    trailing_stop = True
    trailing_stop_positive = 0.03
    trailing_stop_positive_offset = 0.04
    trailing_only_offset_is_reached = True

    # 時間利確はしない(旧エンジン同様、トレンドが続く限り引っ張る)
    minimal_roi = {"0": 100}

    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                 max_leverage, entry_tag, side, **kwargs) -> float:
        return 2.0  # 標準型プリセットと同じレバレッジ

    def populate_indicators(self, dataframe, metadata):
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=12)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=26)
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe.loc[
            (dataframe["ema_fast"] < dataframe["ema_slow"])
            & (dataframe["ema_fast"].shift(1) >= dataframe["ema_slow"].shift(1))
            & (dataframe["volume"] > 0),
            "enter_short"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe.loc[
            (dataframe["ema_fast"] > dataframe["ema_slow"])
            & (dataframe["ema_fast"].shift(1) <= dataframe["ema_slow"].shift(1)),
            "exit_short"] = 1
        return dataframe
