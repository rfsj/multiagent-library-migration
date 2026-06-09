from __future__ import annotations

import pandas as pd


def load_active_products(path):
    df = pd.read_csv(path)
    return (
        df[df["active"] == True][["product_id", "name", "category", "unit_price"]]
        .sort_values("product_id")
        .reset_index(drop=True)
    )


def products_by_category(path):
    df = pd.read_csv(path)
    active = df[df["active"] == True]
    return (
        active.groupby("category", as_index=False)
        .agg(product_count=("product_id", "count"), avg_price=("unit_price", "mean"))
        .round({"avg_price": 2})
        .sort_values("category")
        .reset_index(drop=True)
    )
