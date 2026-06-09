from __future__ import annotations

import pandas as pd


def cumulative_revenue_by_product(path):
    df = pd.read_csv(path)
    df = df.sort_values(["product_id", "date"])
    df["cumulative_revenue"] = df.groupby("product_id")["revenue"].cumsum()
    return df[["product_id", "date", "revenue", "cumulative_revenue"]].reset_index(drop=True)


def rolling_daily_average(path, window=3):
    df = pd.read_csv(path)
    df = df.sort_values("date").reset_index(drop=True)
    df["rolling_avg"] = df["revenue"].rolling(window, min_periods=1).mean().round(2)
    return df[["date", "revenue", "rolling_avg"]]


def daily_growth_rate(path):
    df = pd.read_csv(path)
    df = df.sort_values("date").reset_index(drop=True)
    df["growth_pct"] = df["revenue"].pct_change().fillna(0).round(4)
    return df[["date", "revenue", "growth_pct"]]
