from __future__ import annotations

import pandas as pd


def revenue_by_category(path):
    df = pd.read_csv(path)
    paid = df[df["status"] == "paid"]
    result = (
        paid.groupby("category", as_index=False)
        .agg(
            total_revenue=("amount", "sum"),
            order_count=("order_id", "nunique"),
        )
        .sort_values("total_revenue", ascending=False)
        .reset_index(drop=True)
    )
    result["total_revenue"] = result["total_revenue"].round(2)
    return result


def invalid_rows(path):
    df = pd.read_csv(path)
    mask = (
        df["amount"].isna()
        | df["category"].isna()
        | ~df["status"].isin(["paid", "pending", "cancelled"])
    )
    return df[mask][["order_id", "status", "amount"]].sort_values("order_id")


def top_categories(path, n=2):
    df = pd.read_csv(path)
    valid = df[df["status"].isin(["paid", "pending"])]
    totals = (
        valid.groupby("category", as_index=False)
        .agg(total=("amount", "sum"))
        .sort_values("total", ascending=False)
    )
    return totals.head(n)
