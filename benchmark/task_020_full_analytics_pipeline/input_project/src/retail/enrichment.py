from __future__ import annotations

import numpy as np
import pandas as pd


def enrich_transactions(txn_path, product_path):
    txns = pd.read_csv(txn_path)
    txns = txns.rename(columns={"txn_id": "transaction_id", "amt": "amount"})
    txns["amount"] = txns["amount"].fillna(0.0)
    txns["transaction_date"] = pd.to_datetime(txns["transaction_date"])

    products = pd.read_csv(product_path)
    products["category_clean"] = products["category"].str.strip().str.lower()
    products["brand_clean"] = products["brand"].str.strip()

    merged = txns.merge(
        products[["product_id", "category_clean", "brand_clean"]],
        on="product_id",
        how="left",
    )
    merged["month"] = merged["transaction_date"].dt.strftime("%Y-%m")
    return (
        merged[
            [
                "transaction_id",
                "customer_id",
                "amount",
                "category_clean",
                "brand_clean",
                "month",
            ]
        ]
        .sort_values("transaction_id")
        .reset_index(drop=True)
    )


def flag_high_value(txn_path, threshold=100):
    df = pd.read_csv(txn_path)
    df = df.rename(columns={"txn_id": "transaction_id", "amt": "amount"})
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    df["is_high_value"] = np.where(df["amount"] > threshold, True, False)
    return (
        df[["transaction_id", "customer_id", "amount", "is_high_value"]]
        .sort_values("transaction_id")
        .reset_index(drop=True)
    )
