from __future__ import annotations

import polars as pl


def available_items(path):
    df = pl.read_csv(path)
    df = df.with_columns([
        pl.col("stock").fill_null(0).cast(pl.Int64),
        pl.col("price").fill_null(0.0)
    ])
    in_stock = df.filter(pl.col("stock") > 0)
    return in_stock.select(["sku", "name", "stock", "price"]).sort(["price", "sku"])


def out_of_stock_items(path):
    df = pl.read_csv(path)
    df = df.with_columns(pl.col("stock").fill_null(0).cast(pl.Int64))
    empty = df.filter(pl.col("stock") == 0)
    return empty.select(["sku", "name"]).sort("sku")


def expensive_items(path, min_price=50.0):
    df = pl.read_csv(path)
    df = df.with_columns(pl.col("price").fill_null(0.0))
    return (
        df.filter(pl.col("price") >= min_price)
        .select(["sku", "name", "price"])
        .sort("price", descending=True)
    )