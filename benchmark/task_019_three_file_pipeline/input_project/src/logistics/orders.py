from __future__ import annotations

import pandas as pd


def order_totals(products_path, orders_path):
    products = pd.read_csv(products_path)
    orders = pd.read_csv(orders_path)
    merged = orders.merge(products[["product_id", "unit_price"]], on="product_id", how="left")
    merged["line_total"] = merged["quantity"] * merged["unit_price"]
    return (
        merged.groupby("order_id", as_index=False)["line_total"]
        .sum()
        .rename(columns={"line_total": "total_amount"})
        .sort_values("order_id")
        .reset_index(drop=True)
    )


def orders_by_customer_category(products_path, orders_path):
    products = pd.read_csv(products_path)
    orders = pd.read_csv(orders_path)
    merged = orders.merge(
        products[["product_id", "unit_price", "category"]], on="product_id", how="left"
    )
    merged["line_total"] = merged["quantity"] * merged["unit_price"]
    return (
        merged.groupby(["customer_id", "category"], as_index=False)["line_total"]
        .sum()
        .rename(columns={"line_total": "total_spent"})
        .sort_values(["customer_id", "category"])
        .reset_index(drop=True)
    )
