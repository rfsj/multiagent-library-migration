from __future__ import annotations

from pathlib import Path

import polars as pl


def load_ticks(path: str | Path):
    ticks = pl.read_csv(path)
    ticks = ticks.with_columns(pl.col("timestamp").str.to_datetime(strict=False))
    ticks = ticks.with_columns(pl.col("price").forward_fill())
    ticks = ticks.filter(pl.col("timestamp").is_not_null() & pl.col("symbol").is_not_null())
    return ticks.sort(["symbol", "timestamp", "trade_id"]).unique(subset=["symbol", "timestamp"], keep="last")


def daily_price_features(path: str | Path):
    ticks = load_ticks(path)
    daily = (
        ticks.group_by_dynamic("timestamp", every="1d", group_by="symbol")
        .agg(
            pl.col("price").last().alias("close"),
            pl.col("volume").sum().alias("volume"),
        )
    )
    daily = daily.with_columns(
        pl.col("close").forward_fill().over("symbol"),
        pl.col("close").pct_change().over("symbol").fill_null(0.0).alias("daily_return"),
    )
    daily = daily.with_columns(
        pl.col("close").rolling_mean(window_size=3, min_periods=1).over("symbol").alias("rolling_3d_close"),
        pl.col("volume").rolling_sum(window_size=3, min_periods=1).over("symbol").alias("rolling_3d_volume"),
    )
    daily = daily.with_columns(
        pl.col("daily_return").round(4),
        pl.col("rolling_3d_close").round(2),
    )
    return daily.sort(["symbol", "timestamp"])


def latest_close_by_symbol(path: str | Path):
    ticks = load_ticks(path)
    latest = ticks.sort(["symbol", "timestamp"]).unique(subset=["symbol"], keep="last")
    return latest.select(["symbol", "timestamp", "price", "volume"]).sort("symbol")