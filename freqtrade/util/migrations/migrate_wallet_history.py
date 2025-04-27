import pandas as pd

from freqtrade.constants import Config
from freqtrade.data.btanalysis.bt_fileutils import trade_list_to_dataframe
from freqtrade.data.btanalysis.trade_parallelism import balance_distribution_over_time
from freqtrade.exchange import Exchange
from freqtrade.exchange.exchange_utils_timeframe import timeframe_to_prev_date
from freqtrade.persistence.key_value_store import KeyValueStore
from freqtrade.persistence.trade_model import Trade
from freqtrade.persistence.wallet_history import WalletBalance
from freqtrade.util.datetime_helpers import dt_now, dt_ts


def migrate_wallet_history(config: Config, exchange: Exchange):
    if not exchange.get_option("ohlcv_has_history", True):
        # we can't fill up wallet history without ohlcv history
        return
    trade_df = trade_list_to_dataframe(Trade.get_trades_proxy())
    if trade_df.empty:
        # no trades, nothing to do
        return
    starting_balance = 1000  # wallets.get_starting_balance()
    pairlist = list(trade_df["pair"].unique())
    timeframe = "1d"
    stake_currency = config["stake_currency"]
    min_date = timeframe_to_prev_date(timeframe, KeyValueStore.get_datetime_value("bot_start_time"))
    balance_dist = balance_distribution_over_time(
        trade_df,
        min_date=min_date,
        max_date=dt_now(),
        start_balance=starting_balance,
        stake_currency=stake_currency,
        timeframe=timeframe,
        pairlist=pairlist,
    )

    data = exchange.refresh_latest_ohlcv(
        [(p, timeframe, config["candle_type_def"]) for p in pairlist],
        since_ms=dt_ts(min_date),
        cache=False,
        drop_incomplete=False,
    )

    dfs = []
    # Combine all dataframes into one using the open rate
    for p, x in data.items():
        x = x.set_index("date", drop=True)
        col = f"{p[0]}_open"
        x[col] = x["open"]
        dfs.append(x[[col]])

    merged = pd.concat(dfs, axis=1)

    balance_dist = balance_dist.join(merged, how="left")
    for p in pairlist:
        balance_dist[f"{p}_value"] = balance_dist[f"{p}_open"] * balance_dist[p]

    balance_dist["total_value"] = balance_dist[
        [f"{p}_value" for p in pairlist] + [stake_currency]
    ].sum(axis=1)

    # Convert balance_dist to WalletBalance entries
    wallet_entries = []
    for date, row in balance_dist.iterrows():
        # Add stake currency entry
        if not pd.isna(row[stake_currency]):
            wallet_entries.append(
                WalletBalance(
                    timestamp=date,
                    currency=stake_currency,
                    price=1.0,  # Stake currency price is always 1.0
                    balance=row[stake_currency],
                )
            )

        # Add entries for each trading pair
        for pair in pairlist:
            base_currency = pair.split("/")[0]
            # Only add entry if balance is not empty/NaN
            if not pd.isna(row[pair]) and row[pair] > 0:
                price_col = f"{pair}_open"
                price = row[price_col] if not pd.isna(row[price_col]) else None

                wallet_entries.append(
                    WalletBalance(
                        timestamp=date, currency=base_currency, price=price, balance=row[pair]
                    )
                )

    # Save entries to database
    if wallet_entries:
        try:
            # Use bulk_save_objects for better performance
            WalletBalance.session.bulk_save_objects(wallet_entries)
            WalletBalance.session.commit()
            print(f"Successfully created {len(wallet_entries)} wallet balance records")
        except Exception as e:
            WalletBalance.session.rollback()
            print(f"Error saving wallet balance records: {e}")
