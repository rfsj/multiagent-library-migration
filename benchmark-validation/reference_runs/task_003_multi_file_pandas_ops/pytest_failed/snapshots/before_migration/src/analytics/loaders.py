from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_orders(path: str | Path):
    orders = pd.read_csv(path)
    orders["order_date"] = pd.to_datetime(orders["order_date"], errors="coerce")
    orders["discount"] = orders["discount"].fillna(0.0)
    orders["gross_revenue"] = orders["quantity"] * orders["unit_price"]
    orders["net_revenue"] = orders["gross_revenue"] * (1 - orders["discount"])
    return orders.sort_values(["order_date", "order_id"]).reset_index(drop=True)


def load_customers(path: str | Path):
    customers = pd.read_csv(path)
    customers["signup_region"] = customers["signup_region"].fillna("unknown")
    return customers.sort_values("customer_id").reset_index(drop=True)


def paid_orders(path: str | Path):
    orders = load_orders(path)
    return orders[orders["status"] == "paid"].copy()
