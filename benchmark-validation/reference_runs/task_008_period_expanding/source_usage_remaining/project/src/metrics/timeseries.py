from __future__ import annotations

import pandas as pd


def monthly_totals(path):
    df = pd.read_csv(path)
    df["period"] = pd.to_datetime(df["date"]).dt.to_period("M").astype(str)
    return (
        df.groupby("period", as_index=False)["amount"]
        .sum()
        .sort_values("period")
        .reset_index(drop=True)
    )


def quarterly_summary(path):
    df = pd.read_csv(path)
    df["quarter"] = pd.to_datetime(df["date"]).dt.to_period("Q").astype(str)
    return (
        df.groupby("quarter", as_index=False)
        .agg(total=("amount", "sum"), count=("amount", "count"))
        .sort_values("quarter")
        .reset_index(drop=True)
    )


def expanding_cumulative(path):
    df = pd.read_csv(path)
    df = df.sort_values("date").reset_index(drop=True)
    df["cum_sum"] = df["amount"].expanding().sum()
    df["cum_mean"] = df["amount"].expanding().mean().round(2)
    return df[["date", "amount", "cum_sum", "cum_mean"]]
