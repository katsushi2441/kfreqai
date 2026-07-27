"""KurageFuturesShortRoi — ショート戦略のROI(時間利確)入り比較用バリアント。

「含み益が+4%のトレール発動に届かず消える」問題への対案として、ロング本番と同様の
時間減衰式の利確を追加した版。エントリー・SL・トレールは本体と同一で、
minimal_roi だけが異なる。バックテスト比較用(2026-07-28)。
"""
from kurage_futures_short import KurageFuturesShortStrategy


class KurageFuturesShortRoi(KurageFuturesShortStrategy):
    # 即時+4% / 1時間後+2% / 4時間後+1% で利確(それ以下はトレール・EMA・SLに任せる)
    minimal_roi = {
        "0": 0.04,
        "60": 0.02,
        "240": 0.01,
    }
