import logging

import pandas as pd

from freqtrade.constants import Config
from freqtrade.data.btanalysis.bt_fileutils import trade_list_to_dataframe
from freqtrade.data.btanalysis.trade_parallelism import balance_distribution_over_time
from freqtrade.exchange import Exchange
from freqtrade.exchange.exchange_utils_timeframe import timeframe_to_prev_date
from freqtrade.persistence import KeyValueStore, Trade, WalletHistory
from freqtrade.util import dt_now, dt_ts


logger = logging.getLogger(__name__)


def migrate_wallet_history(config: Config, exchange: Exchange, starting_balance: float):
    if not exchange.get_option("ohlcv_has_history", True):
        # we can't fill up wallet history without ohlcv history
        return
    if KeyValueStore.get_int_value("wallet_history_migration"):
        logger.debug("Wallet history migration already completed.")
        return
    logger.info("Starting wallet history migration...")
    _migrate_wallet_history(config, exchange, starting_balance)
    logger.info("Wallet history migration completed.")
    KeyValueStore.store_value("wallet_history_migration", 1)


def _migrate_wallet_history(config: Config, exchange: Exchange, starting_balance: float):
    trade_df = trade_list_to_dataframe(Trade.get_trades_proxy(), minified=False)
    if trade_df.empty:
        # no trades, nothing to do
        return
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
    pairlist_valid = [p for p in pairlist if p in exchange.markets]

    data = exchange.refresh_latest_ohlcv(
        [(p, timeframe, config["candle_type_def"]) for p in pairlist_valid],
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

    if not dfs:
        logger.warning(
            "No OHLCV data available for the trading pairs; skipping wallet history migration."
        )
        return
    merged = pd.concat(dfs, axis=1)

    balance_dist = balance_dist.join(merged, how="left")
    for p in pairlist_valid:
        balance_dist[f"{p}_value"] = balance_dist[f"{p}_open"] * balance_dist[p]

    balance_dist["total_value"] = balance_dist[
        [f"{p}_value" for p in pairlist_valid] + [stake_currency]
    ].sum(axis=1)

    # Precompute column indices for faster tuple-based iteration
    # Assume the first column is the index (date)
    stake_idx = balance_dist.columns.get_loc(stake_currency)
    pair_balance_idx = {pair: balance_dist.columns.get_loc(pair) + 1 for pair in pairlist_valid}
    pair_leverage_idx = {
        pair: balance_dist.columns.get_loc(f"{pair}_leverage") + 1 for pair in pairlist_valid
    }
    pair_price_idx = {
        pair: balance_dist.columns.get_loc(f"{pair}_open") + 1 for pair in pairlist_valid
    }

    # Convert balance_dist to WalletHistory entries
    wallet_entries = []
    for row in balance_dist.itertuples(index=True, name=None):
        date = row[0]

        # Add stake currency entry
        stake_balance = row[stake_idx + 1]
        if not pd.isna(stake_balance):
            wallet_entries.append(
                WalletHistory(
                    timestamp=date,
                    currency=stake_currency,
                    price=1.0,  # Stake currency price is always 1.0
                    balance=stake_balance,
                )
            )

        # Add entries for each trading pair
        for pair in pairlist_valid:
            base_currency = pair.split("/")[0]
            balance_value = row[pair_balance_idx[pair]]
            leverage_value = row[pair_leverage_idx[pair]]
            # Only add entry if balance is not empty/NaN
            if not pd.isna(balance_value) and balance_value > 0:
                price_value = row[pair_price_idx[pair]]
                price = price_value if not pd.isna(price_value) else None

                wallet_entries.append(
                    WalletHistory(
                        timestamp=date,
                        currency=base_currency,
                        price=price,
                        balance=balance_value,
                        leverage=leverage_value if not pd.isna(leverage_value) else 1.0,
                    )
                )

    # Save entries to database
    if wallet_entries:
        try:
            # Use bulk_save_objects for better performance
            WalletHistory.session.bulk_save_objects(wallet_entries)
            WalletHistory.session.commit()
            KeyValueStore.store_value("wallet_history_migration_date", dt_now())
            logger.info(f"Successfully created {len(wallet_entries)} wallet balance records")
        except Exception as e:
            WalletHistory.session.rollback()
            logger.error(f"Error saving wallet balance records: {e}")
