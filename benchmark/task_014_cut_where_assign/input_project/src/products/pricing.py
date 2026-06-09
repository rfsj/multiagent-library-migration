from __future__ import annotations

import numpy as np
import pandas as pd


def price_tier(path):
    df = pd.read_csv(path)
    df["tier"] = pd.cut(
        df["price"],
        bins=[0, 25, 75, float("inf")],
        labels=["budget", "mid", "premium"],
    ).astype(str)
    return (
        df[["product_id", "name", "price", "tier"]]
        .sort_values("product_id")
        .reset_index(drop=True)
    )


def apply_discount(path):
    df = pd.read_csv(path)
    df["discounted_price"] = np.where(
        df["stock"] > 100,
        (df["price"] * 0.9).round(2),
        df["price"],
    )
    return (
        df[["product_id", "price", "stock", "discounted_price"]]
        .sort_values("product_id")
        .reset_index(drop=True)
    )


def enrich_with_margin(path):
    df = pd.read_csv(path)
    df = df.assign(
        margin=((df["price"] - df["cost"]) / df["price"] * 100).round(1),
        high_margin=lambda x: x["margin"] > 30,
    )
    return (
        df[["product_id", "price", "cost", "margin", "high_margin"]]
        .sort_values("product_id")
        .reset_index(drop=True)
    )
