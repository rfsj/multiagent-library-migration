from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_ticks(path: str | Path):
    ticks = pd.read_csv(path)
    ticks["timestamp"] = pd.to_datetime(ticks["timestamp"], errors="coerce")
    ticks["price"] = ticks["price"].fillna(method="ffill")
    ticks = ticks[ticks["timestamp"].notna() & ticks["symbol"].notna()].copy()
    return (
        ticks.sort_values(["symbol", "timestamp", "trade_id"])
        .drop_duplicates(
            subset=["symbol", "timestamp"],
            keep="last",
        )
        .reset_index(drop=True)
    )


def daily_price_features(path: str | Path):
    ticks = load_ticks(path)
    daily = (
        ticks.set_index("timestamp")
        .groupby("symbol")
        .resample("D")
        .agg(close=("price", "last"), volume=("volume", "sum"))
        .drop(columns=["symbol"], errors="ignore")
        .reset_index()
    )
    daily["close"] = daily.groupby("symbol")["close"].ffill()
    daily["daily_return"] = daily.groupby("symbol")["close"].pct_change().fillna(0.0)
    daily["rolling_3d_close"] = (
        daily.groupby("symbol")["close"]
        .rolling(window=3, min_periods=1)
        .mean()
        .reset_index(level=0, drop=True)
    )
    daily["rolling_3d_volume"] = (
        daily.groupby("symbol")["volume"]
        .rolling(window=3, min_periods=1)
        .sum()
        .reset_index(level=0, drop=True)
    )
    daily["daily_return"] = daily["daily_return"].round(4)
    daily["rolling_3d_close"] = daily["rolling_3d_close"].round(2)
    return daily.sort_values(["symbol", "timestamp"]).reset_index(drop=True)


def latest_close_by_symbol(path: str | Path):
    ticks = load_ticks(path)
    latest = ticks.sort_values(["symbol", "timestamp"]).drop_duplicates(
        subset=["symbol"],
        keep="last",
    )
    return latest[["symbol", "timestamp", "price", "volume"]].sort_values("symbol")
