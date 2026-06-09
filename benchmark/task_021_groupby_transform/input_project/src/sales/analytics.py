from __future__ import annotations

import pandas as pd


def add_region_share(path):
    df = pd.read_csv(path)
    df["region_total"] = df.groupby("region")["revenue"].transform("sum")
    df["share"] = (df["revenue"] / df["region_total"]).round(4)
    return (
        df[["sale_id", "region", "revenue", "share"]]
        .sort_values("sale_id")
        .reset_index(drop=True)
    )


def add_category_deviation(path):
    df = pd.read_csv(path)
    df["cat_mean"] = df.groupby("category")["price"].transform("mean")
    df["deviation"] = (df["price"] - df["cat_mean"]).round(2)
    return (
        df[["product_id", "category", "price", "deviation"]]
        .sort_values("product_id")
        .reset_index(drop=True)
    )


def rank_within_group(path):
    df = pd.read_csv(path)
    df["rank"] = (
        df.groupby("region")["revenue"]
        .transform(lambda x: x.rank(ascending=False, method="dense"))
        .astype(int)
    )
    return (
        df[["sale_id", "region", "revenue", "rank"]]
        .sort_values(["region", "rank"])
        .reset_index(drop=True)
    )
