from __future__ import annotations

import pandas as pd


def load_transactions(path):
    df = pd.read_csv(path)
    df = df.rename(columns={"txn_id": "transaction_id", "amt": "amount"})
    df["amount"] = df["amount"].fillna(0.0)
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    return (
        df.dropna(subset=["customer_id", "product_id"])
        .sort_values("transaction_id")
        .reset_index(drop=True)
    )


def load_customers(path):
    df = pd.read_csv(path)
    df["email"] = df["email"].str.strip().str.lower()
    df["signup_date"] = pd.to_datetime(df["signup_date"])
    df["age_group"] = pd.cut(
        df["age"],
        bins=[0, 35, 55, 100],
        labels=["young", "middle", "senior"],
    ).astype(str)
    return (
        df[["customer_id", "email", "age_group", "signup_date", "segment"]]
        .sort_values("customer_id")
        .reset_index(drop=True)
    )
