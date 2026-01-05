import logging
from datetime import datetime

import numpy as np
import pandas as pd

from freqtrade.constants import IntOrInf
from freqtrade.exchange.exchange_utils_timeframe import (
    timeframe_to_prev_date,
    timeframe_to_resample_freq,
)
from freqtrade.util import dt_from_ts


logger = logging.getLogger(__name__)


def analyze_trade_parallelism(trades: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """
    Find overlapping trades by expanding each trade once per period it was open
    and then counting overlaps.
    :param trades: Trades Dataframe - can be loaded from backtest, or created
        via trade_list_to_dataframe
    :param timeframe: Timeframe used for backtest
    :return: dataframe with open-counts per time-period in timeframe
    """
    from freqtrade.exchange import timeframe_to_resample_freq

    timeframe_freq = timeframe_to_resample_freq(timeframe)
    dates = [
        pd.Series(
            pd.date_range(
                row[1]["open_date"],
                row[1]["close_date"],
                freq=timeframe_freq,
                # Exclude right boundary - the date is the candle open date.
                inclusive="left",
            )
        )
        for row in trades[["open_date", "close_date"]].iterrows()
    ]
    deltas = [len(x) for x in dates]
    dates = pd.Series(pd.concat(dates).values, name="date")
    df2 = pd.DataFrame(np.repeat(trades.values, deltas, axis=0), columns=trades.columns)

    df2 = pd.concat([dates, df2], axis=1)
    df2 = df2.set_index("date")
    df_final = df2.resample(timeframe_freq)[["pair"]].count()
    df_final = df_final.rename({"pair": "open_trades"}, axis=1)
    return df_final


def evaluate_result_multi(
    trades: pd.DataFrame, timeframe: str, max_open_trades: IntOrInf
) -> pd.DataFrame:
    """
    Find overlapping trades by expanding each trade once per period it was open
    and then counting overlaps
    :param trades: Trades Dataframe - can be loaded from backtest, or created
        via trade_list_to_dataframe
    :param timeframe: Frequency used for the backtest
    :param max_open_trades: parameter max_open_trades used during backtest run
    :return: dataframe with open-counts per time-period in freq
    """
    df_final = analyze_trade_parallelism(trades, timeframe)
    return df_final[df_final["open_trades"] > max_open_trades]


def balance_distribution_over_time(
    trades: pd.DataFrame,
    min_date: datetime,
    max_date: datetime,
    timeframe: str,
    stake_currency: str,
    start_balance: float,
    pairlist: list[str],
) -> pd.DataFrame:
    """
    Return a dataframe with stake_currency and the pairlist as columns
    Each column will contain the amount of the currency at the given time
    :param trades: Trades Dataframe - can be loaded from backtest, or created
        via trade_list_to_dataframe
    :param timeframe: Frequency to use for the resulting dataframe
    :param min_date: start date
    :param max_date: End date (will be rounded down to timeframe)
    :param stake_currency: The stake currency
    :param start_balance: Starting balance in stake currency
    :param pairlist: List of trading pairs to include in the dataframe
        Can be obtained via trade_df["pair"].unique()
        For pairs without trades, the column will be all zeros
    :return: Dataframe with balance distribution over time
    """
    min_date_res = timeframe_to_prev_date(timeframe, min_date)
    max_date_res = timeframe_to_prev_date(timeframe, max_date)
    index = pd.date_range(min_date_res, max_date_res, freq=timeframe_to_resample_freq(timeframe))
    df = pd.DataFrame(index=index)
    df[stake_currency] = float(start_balance)
    df[pairlist] = 0.0
    for trade in trades.sort_values(by=["open_date"]).itertuples():
        end_date = trade.close_date if trade.close_date is not pd.NaT else None
        # Exclude open orders - these won't have order_filled_timestamp set.
        orders = [o for o in trade.orders if o["order_filled_timestamp"]]
        for order in sorted(orders, key=lambda x: x["order_filled_timestamp"]):
            filled_at = pd.Timestamp(dt_from_ts(order["order_filled_timestamp"]))
            real_amount = order.get("filled", order["amount"]) / trade.leverage
            stake = order["safe_price"] * real_amount
            if order["ft_is_entry"]:
                fee = stake * trade.fee_open
                df.loc[filled_at:end_date, trade.pair] += real_amount
                df.loc[filled_at:, stake_currency] -= stake + fee
            else:
                fee = stake * trade.fee_close
                df.loc[filled_at:end_date, trade.pair] -= real_amount
                df.loc[filled_at:, stake_currency] += stake - fee

    # Round to avoid floating point issues
    df = df.round(14)
    return df
