from __future__ import annotations

import pandas as pd


def sales_pivot(path):
    df = pd.read_csv(path)
    result = df.pivot_table(
        index="region",
        columns="category",
        values="amount",
        aggfunc="sum",
        fill_value=0,
    ).reset_index()
    result.columns.name = None
    return result.sort_values("region").reset_index(drop=True)


def top_product_per_region(path):
    df = pd.read_csv(path)
    df["rank"] = df.groupby("region")["amount"].rank(method="dense", ascending=False)
    return (
        df[df["rank"] == 1][["region", "product", "amount"]]
        .sort_values("region")
        .reset_index(drop=True)
    )


def region_category_stats(path):
    df = pd.read_csv(path)
    return (
        df.groupby(["region", "category"], as_index=False)
        .agg(
            total=("amount", "sum"),
            avg=("amount", "mean"),
            count=("amount", "count"),
        )
        .round({"avg": 2})
        .sort_values(["region", "category"])
        .reset_index(drop=True)
    )
