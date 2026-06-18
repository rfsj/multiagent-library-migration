from __future__ import annotations

from pathlib import Path

import pandas as pd

from finance.prices import daily_price_features


def load_signals(path: str | Path):
    signals = pd.read_csv(path)
    signals["timestamp"] = pd.to_datetime(signals["timestamp"], errors="coerce")
    signals["signal"] = signals["signal"].str.strip().str.lower()
    signals["confidence"] = signals["confidence"].fillna(0.0)
    signals = signals[signals["timestamp"].notna() & signals["symbol"].notna()].copy()
    return signals.sort_values(["symbol", "timestamp", "signal_id"]).reset_index(
        drop=True
    )


def align_signals_to_prices(ticks_path: str | Path, signals_path: str | Path):
    prices = daily_price_features(ticks_path)
    signals = load_signals(signals_path)
    prices = prices.sort_values(["timestamp", "symbol"])
    signals = signals.sort_values(["timestamp", "symbol"])
    aligned = pd.merge_asof(
        prices,
        signals,
        by="symbol",
        on="timestamp",
        direction="backward",
        tolerance=pd.Timedelta(days=2),
    )
    aligned["signal"] = aligned["signal"].fillna("hold")
    aligned["confidence"] = aligned["confidence"].fillna(0.0)
    aligned["score"] = aligned["daily_return"] * aligned["confidence"]
    aligned["score"] = aligned["score"].round(4)
    return (
        aligned[
            [
                "symbol",
                "timestamp",
                "close",
                "daily_return",
                "signal",
                "confidence",
                "score",
            ]
        ]
        .sort_values(["symbol", "timestamp"])
        .reset_index(drop=True)
    )


def signal_performance_summary(ticks_path: str | Path, signals_path: str | Path):
    aligned = align_signals_to_prices(ticks_path, signals_path)
    actionable = aligned[aligned["signal"].isin(["buy", "sell"])].copy()
    actionable["signed_return"] = actionable.apply(
        lambda row: (
            row["daily_return"] if row["signal"] == "buy" else -row["daily_return"]
        ),
        axis=1,
    )
    result = (
        actionable.groupby(["symbol", "signal"], as_index=False)
        .agg(
            observations=("timestamp", "nunique"),
            average_score=("score", "mean"),
            total_signed_return=("signed_return", "sum"),
        )
        .sort_values(["symbol", "signal"])
        .reset_index(drop=True)
    )
    result["average_score"] = result["average_score"].round(4)
    result["total_signed_return"] = result["total_signed_return"].round(4)
    return result
