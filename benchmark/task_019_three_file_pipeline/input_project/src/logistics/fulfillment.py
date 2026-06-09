from __future__ import annotations

import pandas as pd


def fulfillment_rate(orders_path, shipments_path):
    orders = pd.read_csv(orders_path)[["order_id", "customer_id"]].drop_duplicates()
    shipments = pd.read_csv(shipments_path)
    shipped = shipments[shipments["status"] == "shipped"][["order_id"]].drop_duplicates()
    merged = orders.merge(shipped, on="order_id", how="left", indicator=True)
    total = len(merged)
    fulfilled = (merged["_merge"] == "both").sum()
    return round(fulfilled / total, 4)


def pending_orders(orders_path, shipments_path):
    orders = pd.read_csv(orders_path)[["order_id", "customer_id"]].drop_duplicates()
    shipments = pd.read_csv(shipments_path)
    shipped_ids = shipments[shipments["status"] == "shipped"]["order_id"].unique()
    return (
        orders[~orders["order_id"].isin(shipped_ids)]
        .sort_values("order_id")
        .reset_index(drop=True)
    )
