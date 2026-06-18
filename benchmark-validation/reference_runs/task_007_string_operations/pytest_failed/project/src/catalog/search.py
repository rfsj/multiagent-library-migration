from __future__ import annotations

import polars as pl


def search_by_keyword(path, keyword):
    df = pl.read_csv(path)
    return df.filter(pl.col("name").str.contains(keyword, case_insensitive=True)).select(["id", "name", "category"]).sort("name")


def normalize_catalog(path):
    df = pl.read_csv(path)
    df = df.with_columns([
        pl.col("category").str.strip_chars().str.to_lowercase(),
        pl.col("name").str.strip_chars()
    ])
    return df.select(["id", "name", "category"]).sort(["category", "name"])


def items_starting_with(path, prefix):
    df = pl.read_csv(path)
    return df.filter(pl.col("name").str.starts_with(prefix)).select(["id", "name"]).sort("id")


def uppercase_names(path):
    df = pl.read_csv(path)
    df = df.with_columns(pl.col("name").str.to_uppercase())
    return df.select(["id", "name"]).sort("id")