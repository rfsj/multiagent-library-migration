from __future__ import annotations

import pandas as pd


def category_distribution(path):
    df = pd.read_csv(path)
    counts = df["category"].value_counts().reset_index()
    return counts.sort_values("category").reset_index(drop=True)


def top_rated_products(path, n=3):
    df = pd.read_csv(path)
    return df.nlargest(n, "rating")[["product_id", "name", "rating"]].reset_index(
        drop=True
    )


def lowest_rated_products(path, n=2):
    df = pd.read_csv(path)
    return df.nsmallest(n, "rating")[["product_id", "name", "rating"]].reset_index(
        drop=True
    )


def brand_unique_categories(path):
    df = pd.read_csv(path)
    return (
        df.groupby("brand", as_index=False)["category"]
        .nunique()
        .rename(columns={"category": "unique_categories"})
        .sort_values("brand")
        .reset_index(drop=True)
    )
