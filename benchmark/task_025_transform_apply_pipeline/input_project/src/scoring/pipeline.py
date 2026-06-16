from __future__ import annotations

import pandas as pd


def enrich_with_history(items_path, history_path):
    items = pd.read_csv(items_path)
    history = pd.read_csv(history_path)
    history["period"] = pd.to_datetime(history["date"]).dt.to_period("M").astype(str)
    monthly = (
        history.groupby(["item_id", "period"], as_index=False)["sales"]
        .sum()
        .sort_values(["item_id", "period"])
    )
    monthly["cum_sales"] = monthly.groupby("item_id")["sales"].transform("cumsum")
    result = monthly.merge(items[["item_id", "category"]], on="item_id", how="left")
    return (
        result[["item_id", "category", "period", "sales", "cum_sales"]]
        .sort_values(["item_id", "period"])
        .reset_index(drop=True)
    )


def flag_anomalies(items_path, history_path):
    items = pd.read_csv(items_path)
    history = pd.read_csv(history_path)
    merged = history.merge(items[["item_id", "category"]], on="item_id", how="left")
    merged["cat_mean"] = merged.groupby("category")["sales"].transform("mean")
    merged["cat_std"] = merged.groupby("category")["sales"].transform("std").fillna(0)
    merged["is_anomaly"] = merged.apply(
        lambda row: (
            bool(abs(row["sales"] - row["cat_mean"]) > 2 * row["cat_std"])
            if row["cat_std"] > 0
            else False
        ),
        axis=1,
    )
    return (
        merged[["item_id", "category", "date", "sales", "is_anomaly"]]
        .sort_values(["item_id", "date"])
        .reset_index(drop=True)
    )
