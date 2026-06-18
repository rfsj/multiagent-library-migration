from __future__ import annotations

import pandas as pd


def available_items(path):
    df = pd.read_csv(path)
    df["stock"] = df["stock"].fillna(0).astype(int)
    df["price"] = df["price"].fillna(0.0)
    in_stock = df[df["stock"] > 0]
    return in_stock[["sku", "name", "stock", "price"]].sort_values(["price", "sku"])


def out_of_stock_items(path):
    df = pd.read_csv(path)
    df["stock"] = df["stock"].fillna(0).astype(int)
    empty = df[df["stock"] == 0]
    return empty[["sku", "name"]].sort_values("sku")


def expensive_items(path, min_price=50.0):
    df = pd.read_csv(path)
    df["price"] = df["price"].fillna(0.0)
    return df[df["price"] >= min_price][["sku", "name", "price"]].sort_values(
        "price", ascending=False
    )
