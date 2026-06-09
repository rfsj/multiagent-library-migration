from __future__ import annotations

import pandas as pd


def category_sales_report(txn_path, product_path):
    txns = pd.read_csv(txn_path)
    txns = txns.rename(columns={"txn_id": "transaction_id", "amt": "amount"})
    txns["amount"] = txns["amount"].fillna(0.0)

    products = pd.read_csv(product_path)
    products["category_clean"] = products["category"].str.strip().str.lower()

    merged = txns.merge(products[["product_id", "category_clean"]], on="product_id", how="left")
    return (
        merged.groupby("category_clean", as_index=False)["amount"]
        .agg(total_sales="sum", avg_transaction="mean", transaction_count="count")
        .round({"total_sales": 2, "avg_transaction": 2})
        .sort_values("total_sales", ascending=False)
        .reset_index(drop=True)
    )


def segment_category_pivot(txn_path, customer_path, product_path):
    txns = pd.read_csv(txn_path)
    txns = txns.rename(columns={"txn_id": "transaction_id", "amt": "amount"})
    txns["amount"] = txns["amount"].fillna(0.0)

    customers = pd.read_csv(customer_path)
    products = pd.read_csv(product_path)
    products["category_clean"] = products["category"].str.strip().str.lower()

    merged = txns.merge(customers[["customer_id", "segment"]], on="customer_id", how="left")
    merged = merged.merge(products[["product_id", "category_clean"]], on="product_id", how="left")

    result = merged.pivot_table(
        index="segment",
        columns="category_clean",
        values="amount",
        aggfunc="sum",
        fill_value=0,
    ).reset_index()
    result.columns.name = None
    return result.sort_values("segment").reset_index(drop=True)
