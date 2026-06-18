from __future__ import annotations

from pathlib import Path

from analytics.loaders import load_orders


def latest_order_per_customer(path: str | Path):
    orders = load_orders(path)
    # Sorting and taking the top row per group using unique or distinct logic
    latest = orders.sort(
        ["customer_id", "order_date", "order_id"],
        descending=[False, True, True],
    ).unique(subset=["customer_id"], keep="first")
    return latest[
        ["customer_id", "order_id", "status", "order_date", "net_revenue"]
    ].sort("customer_id")


def invalid_order_rows(path: str | Path):
    import polars as pl
    orders = load_orders(path)
    mask = (
        orders["order_date"].is_null()
        | (orders["quantity"] <= 0)
        | (orders["unit_price"] < 0)
        | (~orders["status"].is_in(["paid", "pending", "cancelled"]))
    )
    return orders.filter(mask)[
        ["order_id", "customer_id", "status", "quantity", "unit_price"]
    ].sort("order_id")