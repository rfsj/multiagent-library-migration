from __future__ import annotations

from pathlib import Path

import polars as pl


def load_orders(path: str | Path):
    orders = pl.read_csv(path)
    orders = orders.with_columns([
        pl.col("order_date").str.to_datetime(errors="coerce"),
        pl.col("discount").fill_null(0.0),
        (pl.col("quantity") * pl.col("unit_price")).alias("gross_revenue")
    ])
    orders = orders.with_columns(
        (pl.col("gross_revenue") * (1 - pl.col("discount"))).alias("net_revenue")
    )
    return orders.sort(["order_date", "order_id"])


def load_customers(path: str | Path):
    customers = pl.read_csv(path)
    customers = customers.with_columns(
        pl.col("signup_region").fill_null("unknown")
    )
    return customers.sort("customer_id")


def paid_orders(path: str | Path):
    orders = load_orders(path)
    return orders.filter(pl.col("status") == "paid")