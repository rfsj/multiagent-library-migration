from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_sales(path: str | Path):
    df = pd.read_csv(path)
    df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
    df["revenue"] = df["quantity"] * df["unit_price"]
    df["discount"] = df["discount"].fillna(0.0)
    df["net_revenue"] = df["revenue"] * (1 - df["discount"])
    return df.sort_values(["order_date", "order_id"]).reset_index(drop=True)


def paid_high_value_orders(path: str | Path, minimum_total: float = 100.0):
    df = load_sales(path)
    filtered = df[(df["status"] == "paid") & (df["net_revenue"] >= minimum_total)]
    return filtered[["order_id", "customer_id", "region", "net_revenue"]].sort_values(
        ["net_revenue", "order_id"], ascending=[False, True]
    )


def revenue_by_region(path: str | Path):
    df = load_sales(path)
    paid = df[df["status"] == "paid"]
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


def customer_lifetime_value(sales_path: str | Path, customers_path: str | Path):
    sales = load_sales(sales_path)
    customers = pd.read_csv(customers_path)
    paid_sales = sales[sales["status"] == "paid"]
    totals = paid_sales.groupby("customer_id", as_index=False).agg(
        total_spend=("net_revenue", "sum"), paid_orders=("order_id", "nunique")
    )
    result = customers.merge(totals, on="customer_id", how="left")
    result["total_spend"] = result["total_spend"].fillna(0.0).round(2)
    result["paid_orders"] = result["paid_orders"].fillna(0).astype(int)
    result["segment"] = result["total_spend"].apply(
        lambda value: "vip" if value >= 250 else "standard"
    )
    return result.sort_values(
        ["segment", "total_spend", "customer_id"], ascending=[False, False, True]
    )


def monthly_product_matrix(path: str | Path):
    df = load_sales(path)
    paid = df[df["status"] == "paid"].copy()
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


def latest_order_per_customer(path: str | Path):
    df = load_sales(path)
    ordered = df.sort_values(
        ["customer_id", "order_date", "order_id"],
        ascending=[True, False, False],
    )
    latest = ordered.drop_duplicates(subset=["customer_id"], keep="first")
    return latest[
        ["customer_id", "order_id", "status", "order_date", "net_revenue"]
    ].sort_values("customer_id")


def invalid_sales_rows(path: str | Path):
    df = load_sales(path)
    mask = (
        df["order_date"].isna()
        | (df["quantity"] <= 0)
        | (df["unit_price"] < 0)
        | ~df["status"].isin(["paid", "pending", "cancelled"])
    )
    return df[mask][["order_id", "customer_id", "status", "quantity", "unit_price"]]
