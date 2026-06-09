from __future__ import annotations

import pandas as pd


def active_customers(path):
    df = pd.read_csv(path)
    return (
        df[df["status"] == "active"][["customer_id", "name", "plan"]]
        .sort_values("customer_id")
        .reset_index(drop=True)
    )


def customers_with_invoices(customers_path, invoices_path):
    customers = pd.read_csv(customers_path)
    invoices = pd.read_csv(invoices_path)
    result = customers.merge(invoices, on="customer_id", how="inner")
    return (
        result[["customer_id", "name", "invoice_id", "amount"]]
        .sort_values(["customer_id", "invoice_id"])
        .reset_index(drop=True)
    )


def customers_without_invoices(customers_path, invoices_path):
    customers = pd.read_csv(customers_path)
    invoices = pd.read_csv(invoices_path)
    merged = customers.merge(
        invoices[["customer_id"]].drop_duplicates(),
        on="customer_id",
        how="left",
        indicator=True,
    )
    return (
        merged[merged["_merge"] == "left_only"][["customer_id", "name"]]
        .sort_values("customer_id")
        .reset_index(drop=True)
    )
