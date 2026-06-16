from __future__ import annotations

import pandas as pd


def invoice_totals_by_plan(customers_path, invoices_path):
    customers = pd.read_csv(customers_path)
    invoices = pd.read_csv(invoices_path)
    merged = invoices.merge(
        customers[["customer_id", "plan"]], on="customer_id", how="left"
    )
    return (
        merged.groupby("plan", as_index=False)["amount"]
        .sum()
        .rename(columns={"amount": "total_amount"})
        .sort_values("plan")
        .reset_index(drop=True)
    )


def all_billing_pairs(customers_path, invoices_path):
    customers = pd.read_csv(customers_path)
    invoices = pd.read_csv(invoices_path)
    result = customers.merge(invoices, on="customer_id", how="outer")
    return (
        result[["customer_id", "name", "invoice_id", "amount"]]
        .sort_values(["customer_id", "invoice_id"])
        .reset_index(drop=True)
    )
