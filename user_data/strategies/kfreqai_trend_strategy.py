"""Kfreqai Trend Strategy — Agent B: 1h breakout-momentum with peak-trail exit.

Same engine as KfreqaiParametricStrategy (breakout-confirmed EMA entry, ride with
peak-PnL trailing exit, cut with stop) but on 1h with backtest-tuned defaults. A
distinct edge from the mean-reversion agent: this buys confirmed breakouts and
rides trends, complementary to buying dips.

Validated (binance 7-pair, 18mo, --cache none): FULL +9.75%, 2025 +10.0%,
2026 H1 -0.23% (robust). Tunable from the dashboard (schema KfreqaiTrendStrategy).
"""
from kfreqai_parametric_strategy import KfreqaiParametricStrategy
from param_schemas import SCHEMAS


class KfreqaiTrendStrategy(KfreqaiParametricStrategy):
    timeframe = "1h"   # trend/breakout persists better and pays fees on 1h, not 5m
    PARAM_SCHEMA = SCHEMAS["KfreqaiTrendStrategy"]
