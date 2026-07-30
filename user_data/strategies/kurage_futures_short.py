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

レジーム連動の出口(2026-07-28、バックテストで実証):
市場の地合い(BTC EMA50 vs EMA200)で最適な出口が変わる。90日18ペアの検証で、
ショートは「bull(上げ相場)では引っ張る」「bear(下げ相場)では時間利確で早逃げ」が
最良(引っ張る固定+$791 → レジーム連動+$1249、+58%)。よって custom_exit で
bear相場のポジションにだけ時間利確(0h+4%/1h+2%/4h+1%)を効かせる。
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
    startup_candle_count = 210  # BTC EMA200(地合い判定)に必要

    stoploss = -0.06
    trailing_stop = True
    trailing_stop_positive = 0.03
    trailing_stop_positive_offset = 0.04
    trailing_only_offset_is_reached = True

    # クラス属性のROIは実質無効(=引っ張る)。bear相場の時間利確は custom_exit で行う
    minimal_roi = {"0": 100}
    use_custom_exit = True

    # bear相場のときだけ効かせる時間利確テーブル(分 -> 利益率)
    BEAR_ROI = {0: 0.04, 60: 0.02, 240: 0.01}

    def informative_pairs(self):
        # 市場全体の地合い判定にBTCの同じ足を使う(全ペア共通)
        return [("BTC/USDT:USDT", self.timeframe)]

    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                 max_leverage, entry_tag, side, **kwargs) -> float:
        return 2.0  # 標準型プリセットと同じレバレッジ

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit,
                    **kwargs):
        """bear相場のポジションにだけ時間減衰式の時間利確を適用する。
        bull相場では何もしない(トレール/EMA/SLに任せて引っ張る)。"""
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df is None or len(df) == 0 or "mkt_bear" not in df.columns:
            return None
        if not bool(df["mkt_bear"].iloc[-1]):
            return None  # bull相場は引っ張る
        held_min = (current_time - trade.open_date_utc).total_seconds() / 60
        for t, r in sorted(self.BEAR_ROI.items()):
            if held_min >= t and current_profit >= r:
                return "bear_time_exit"
        return None

    def populate_indicators(self, dataframe, metadata):
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=12)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=26)
        # 市場地合い: BTCのEMA50<EMA200 を bear とみなす(全ペア共通のフラグ)
        btc, _ = self.dp.get_analyzed_dataframe("BTC/USDT:USDT", self.timeframe)
        if btc is not None and len(btc) and "close" in btc:
            e50 = ta.EMA(btc, timeperiod=50)
            e200 = ta.EMA(btc, timeperiod=200)
            bear = (e50 < e200)
            bear.index = btc["date"]
            m = dataframe.set_index("date")
            m["mkt_bear"] = bear.reindex(m.index).ffill().fillna(True)
            dataframe["mkt_bear"] = m["mkt_bear"].values
        else:
            dataframe["mkt_bear"] = True  # 取得できないときは安全側(bear=時間利確)
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        # bear地合い(BTC EMA50<EMA200)のときだけ新規ショート(2026-07-30採用)。
        # 90日/30日の2窓バックテストで一貫して改善(90日: -4,330→-3,235・DD50%→36% /
        # 7月: -1,680→-1,273・DD22%→13%)。EMAデッドクロス常時ショートは上げ相場・
        # チョップで踏み上げ損切りを繰り返す(2026-07-30実測: 1日で損切り4連発-249 USDT。
        # 入場時の4h騰落率フィルタは全滅を防げず逆効果と実測済み=bt_variants参照)。
        # 検証: user_data/strategies/kurage_futures_short_bt_variants.py + binance 1h
        dataframe.loc[
            (dataframe["ema_fast"] < dataframe["ema_slow"])
            & (dataframe["ema_fast"].shift(1) >= dataframe["ema_slow"].shift(1))
            & (dataframe["mkt_bear"].astype(bool))
            & (dataframe["volume"] > 0),
            "enter_short"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe.loc[
            (dataframe["ema_fast"] > dataframe["ema_slow"])
            & (dataframe["ema_fast"].shift(1) <= dataframe["ema_slow"].shift(1)),
            "exit_short"] = 1
        return dataframe
