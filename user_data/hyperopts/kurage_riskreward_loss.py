"""KurageRiskRewardLoss — 「コツコツドカン」を直接罰する評価関数。

標準のhyperopt損失関数は勝率や総利益に寄りがちで、+1%を積んで-7%を食らう
戦略でも「勝率67%・利益それなり」なら高評価になってしまう。この関数は
kfreqaiの実害(2026-07-10のLAB/PLB/TAC事故)を踏まえ、次の4要素で採点する:

  score = 総利益 × RR係数 × ドカン係数 × DD係数   (利益が負のときは係数で除算)

- RR係数     = 2·payoff/(payoff+1)。payoff=平均勝ち/平均負け。
               RR1.0→1.00 / RR2.0→1.33 / RR0.5→0.67。勝率は直接見ない。
- ドカン係数 = 最悪の1敗が平均勝ちの何倍かへのペナルティ。
               tolerance=4: 最悪敗が平均勝ちの5倍で係数1/2、9倍で1/3。
- DD係数     = 1 - 相対資産DD×2 (下限0.1)。DD20%なら0.6倍。
- 取引数が min_trades 未満は問答無用で失格(過学習した「奇跡の3トレード」対策)。

小さいほど良い(freqtradeの規約)ので、返り値は -score。
"""

from pandas import DataFrame

from freqtrade.data.metrics import calculate_max_drawdown
from freqtrade.optimize.hyperopt import IHyperOptLoss


MIN_TRADES = 200        # 30日160銘柄なら十分下回らない水準
DOKAN_TOLERANCE = 4.0   # 小さいほど「1発の深傷」に厳しい
DD_MULT = 2.0           # 大きいほどドローダウンに厳しい
LARGE_NUMBER = 1e9


class KurageRiskRewardLoss(IHyperOptLoss):
    @staticmethod
    def hyperopt_loss_function(
        results: DataFrame,
        trade_count: int,
        starting_balance: float,
        *args,
        **kwargs,
    ) -> float:
        if trade_count < MIN_TRADES:
            return LARGE_NUMBER

        profit_total = results["profit_abs"].sum()
        wins = results.loc[results["profit_abs"] > 0, "profit_abs"]
        losses = results.loc[results["profit_abs"] <= 0, "profit_abs"]

        if wins.empty:
            return LARGE_NUMBER
        avg_win = wins.mean()
        if losses.empty:
            payoff = 5.0
            worst_ratio = 0.0
        else:
            payoff = avg_win / abs(losses.mean())
            worst_ratio = abs(losses.min()) / max(avg_win, 1e-9)

        rr_factor = 2 * payoff / (payoff + 1)
        dokan_factor = 1 / (1 + max(0.0, worst_ratio - 1) / DOKAN_TOLERANCE)

        try:
            dd = calculate_max_drawdown(
                results, starting_balance=starting_balance, value_col="profit_abs"
            ).relative_account_drawdown
        except ValueError:
            dd = 0.0
        dd_factor = max(0.1, 1 - dd * DD_MULT)

        quality = rr_factor * dokan_factor * dd_factor
        if profit_total >= 0:
            score = profit_total * quality
        else:
            # 赤字のときは質が悪いほどさらに悪い点にする(係数で割る)
            score = profit_total / max(quality, 0.1)
        return -score
