from __future__ import annotations

from pathlib import Path

from analytics.loaders import load_orders


def latest_order_per_customer(path: str | Path):
    orders = load_orders(path)
    ordered = orders.sort_values(
        ["customer_id", "order_date", "order_id"],
        ascending=[True, False, False],
    )
    latest = ordered.drop_duplicates(subset=["customer_id"], keep="first")
    return latest[
        ["customer_id", "order_id", "status", "order_date", "net_revenue"]
    ].sort_values("customer_id")


def invalid_order_rows(path: str | Path):
    orders = load_orders(path)
    mask = (
        orders["order_date"].isna()
        | (orders["quantity"] <= 0)
        | (orders["unit_price"] < 0)
        | ~orders["status"].isin(["paid", "pending", "cancelled"])
    )
    return orders[mask][
        ["order_id", "customer_id", "status", "quantity", "unit_price"]
    ].sort_values("order_id")
