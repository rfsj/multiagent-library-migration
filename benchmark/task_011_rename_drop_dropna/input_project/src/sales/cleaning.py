from __future__ import annotations

import pandas as pd


def standardize_columns(path):
    df = pd.read_csv(path)
    df = df.rename(columns={"cust_id": "customer_id", "rev": "revenue", "qty": "quantity"})
    return df[["customer_id", "revenue", "quantity"]].sort_values("customer_id").reset_index(drop=True)


def remove_incomplete(path):
    df = pd.read_csv(path)
    df = df.dropna(subset=["revenue", "quantity"])
    return df.sort_values("sale_id").reset_index(drop=True)


def clean_for_reporting(path):
    df = pd.read_csv(path)
    df = df[df["revenue"] > 0]
    df = df.drop(columns=["internal_flag", "notes"])
    return df.sort_values("sale_id").reset_index(drop=True)
