from __future__ import annotations

from pathlib import Path

import polars as pl

from finance.prices import daily_price_features


def load_signals(path: str | Path):
    signals = pl.read_csv(path)
    signals = signals.with_columns(
        pl.col("timestamp").str.to_datetime(errors="coerce"),
        pl.col("signal").str.strip_chars().str.to_lowercase(),
        pl.col("confidence").fill_null(0.0)
    )
    signals = signals.filter(pl.col("timestamp").is_not_null() & pl.col("symbol").is_not_null())
    return signals.sort(["symbol", "timestamp", "signal_id"])


def align_signals_to_prices(ticks_path: str | Path, signals_path: str | Path):
    prices = daily_price_features(ticks_path)
    signals = load_signals(signals_path)
    prices = prices.sort(["timestamp", "symbol"])
    signals = signals.sort(["timestamp", "symbol"])
    aligned = prices.join_asof(
        signals,
        by="symbol",
        on="timestamp",
        strategy="backward",
        tolerance="2d",
    )
    aligned = aligned.with_columns(
        pl.col("signal").fill_null("hold"),
        pl.col("confidence").fill_null(0.0)
    )
    aligned = aligned.with_columns(
        (pl.col("daily_return") * pl.col("confidence")).round(4).alias("score")
    )
    return aligned.select(["symbol", "timestamp", "close", "daily_return", "signal", "confidence", "score"]).sort(["symbol", "timestamp"])


def signal_performance_summary(ticks_path: str | Path, signals_path: str | Path):
    aligned = align_signals_to_prices(ticks_path, signals_path)
    actionable = aligned.filter(pl.col("signal").is_in(["buy", "sell"]))
    actionable = actionable.with_columns(
        pl.when(pl.col("signal") == "buy")
        .then(pl.col("daily_return"))
        .otherwise(-pl.col("daily_return"))
        .alias("signed_return")
    )
    result = (
        actionable.group_by(["symbol", "signal"])
        .agg(
            pl.col("timestamp").n_unique().alias("observations"),
            pl.col("score").mean().round(4).alias("average_score"),
            pl.col("signed_return").sum().round(4).alias("total_signed_return"),
        )
        .sort(["symbol", "signal"])
    )
    return result