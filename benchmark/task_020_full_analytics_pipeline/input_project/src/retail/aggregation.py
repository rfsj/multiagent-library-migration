from __future__ import annotations

import pandas as pd


def monthly_revenue_by_segment(txn_path, customer_path):
    txns = pd.read_csv(txn_path)
    txns = txns.rename(columns={"txn_id": "transaction_id", "amt": "amount"})
    txns["amount"] = txns["amount"].fillna(0.0)
    txns["month"] = pd.to_datetime(txns["transaction_date"]).dt.strftime("%Y-%m")

    customers = pd.read_csv(customer_path)

    merged = txns.merge(
        customers[["customer_id", "segment"]], on="customer_id", how="left"
    )
    return (
        merged.groupby(["segment", "month"], as_index=False)["amount"]
        .sum()
        .sort_values(["segment", "month"])
        .reset_index(drop=True)
    )


def rolling_revenue_trend(txn_path, window=3):
    df = pd.read_csv(txn_path)
    df = df.rename(columns={"txn_id": "transaction_id", "amt": "amount"})
    df["amount"] = df["amount"].fillna(0.0)
    daily = (
        df.groupby("transaction_date", as_index=False)["amount"]
        .sum()
        .sort_values("transaction_date")
        .reset_index(drop=True)
    )
    daily["rolling_avg"] = (
        daily["amount"].rolling(window, min_periods=1).mean().round(2)
    )
    return daily[["transaction_date", "amount", "rolling_avg"]]
