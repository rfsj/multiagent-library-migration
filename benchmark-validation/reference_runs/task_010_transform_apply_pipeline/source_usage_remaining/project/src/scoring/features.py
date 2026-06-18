from __future__ import annotations

import pandas as pd


def add_group_stats(path):
    df = pd.read_csv(path)
    df["group_mean"] = df.groupby("category")["value"].transform("mean")
    df["group_std"] = df.groupby("category")["value"].transform("std").fillna(0)
    df["z_score"] = df.apply(
        lambda row: (
            round((row["value"] - row["group_mean"]) / row["group_std"], 2)
            if row["group_std"] > 0
            else 0.0
        ),
        axis=1,
    )
    return (
        df[["item_id", "category", "value", "group_mean", "z_score"]]
        .sort_values("item_id")
        .reset_index(drop=True)
    )


def add_rank_and_share(path):
    df = pd.read_csv(path)
    df["group_total"] = df.groupby("category")["value"].transform("sum")
    df["rank"] = (
        df.groupby("category")["value"]
        .transform(lambda x: x.rank(ascending=False, method="dense"))
        .astype(int)
    )
    df["share"] = (df["value"] / df["group_total"]).round(4)
    return (
        df[["item_id", "category", "value", "rank", "share"]]
        .sort_values(["category", "rank"])
        .reset_index(drop=True)
    )
