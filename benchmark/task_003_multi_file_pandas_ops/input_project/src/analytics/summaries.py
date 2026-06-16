from __future__ import annotations

from pathlib import Path

import pandas as pd

from analytics.loaders import load_customers, paid_orders


def revenue_by_region(path: str | Path):
    paid = paid_orders(path)
    result = (
        paid.groupby("region", as_index=False)
        .agg(
            total_revenue=("net_revenue", "sum"),
            orders=("order_id", "nunique"),
            average_order_value=("net_revenue", "mean"),
        )
        .sort_values(["total_revenue", "region"], ascending=[False, True])
        .reset_index(drop=True)
    )
    result["total_revenue"] = result["total_revenue"].round(2)
    result["average_order_value"] = result["average_order_value"].round(2)
    return result


def customer_lifetime_value(orders_path: str | Path, customers_path: str | Path):
    customers = load_customers(customers_path)
    paid = paid_orders(orders_path)
    totals = paid.groupby("customer_id", as_index=False).agg(
        total_spend=("net_revenue", "sum"), paid_orders=("order_id", "nunique")
    )
    result = customers.merge(totals, on="customer_id", how="left")
    result["total_spend"] = result["total_spend"].fillna(0.0).round(2)
    result["paid_orders"] = result["paid_orders"].fillna(0).astype(int)
    result["segment"] = result["total_spend"].apply(
        lambda value: "vip" if value >= 250 else "standard"
    )
    return result.sort_values(
        ["segment", "total_spend", "customer_id"],
        ascending=[False, False, True],
    ).reset_index(drop=True)


def monthly_product_matrix(path: str | Path):
    paid = paid_orders(path)
    paid["month"] = paid["order_date"].dt.strftime("%Y-%m")
    matrix = pd.pivot_table(
        paid,
        values="net_revenue",
        index="month",
        columns="product",
        aggfunc="sum",
        fill_value=0.0,
    )
    matrix = matrix.reset_index()
    product_columns = [column for column in matrix.columns if column != "month"]
    matrix[product_columns] = matrix[product_columns].round(2)
    return matrix.sort_values("month").reset_index(drop=True)
