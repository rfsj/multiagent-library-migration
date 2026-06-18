from __future__ import annotations

from pathlib import Path

import polars as pl


def load_sales(path: str | Path):
    df = pl.read_csv(path)
    df = df.with_columns(
        pl.col("order_date").str.to_datetime(strict=False),
        (pl.col("quantity") * pl.col("unit_price")).alias("revenue"),
        pl.col("discount").fill_null(0.0)
    )
    df = df.with_columns(
        (pl.col("revenue") * (1 - pl.col("discount"))).alias("net_revenue")
    )
    return df.sort(["order_date", "order_id"])


def paid_high_value_orders(path: str | Path, minimum_total: float = 100.0):
    df = load_sales(path)
    filtered = df.filter((pl.col("status") == "paid") & (pl.col("net_revenue") >= minimum_total))
    return filtered.select(
        ["order_id", "customer_id", "region", "net_revenue"]
    ).sort(["net_revenue", "order_id"], descending=[True, False])


def revenue_by_region(path: str | Path):
    df = load_sales(path)
    paid = df.filter(pl.col("status") == "paid")
    result = (
        paid.group_by("region")
        .agg(
            total_revenue=pl.col("net_revenue").sum(),
            orders=pl.col("order_id").n_unique(),
            average_order_value=pl.col("net_revenue").mean(),
        )
        .sort(["total_revenue", "region"], descending=[True, False])
    )
    return result.with_columns(
        pl.col("total_revenue").round(2),
        pl.col("average_order_value").round(2)
    )


def customer_lifetime_value(sales_path: str | Path, customers_path: str | Path):
    sales = load_sales(sales_path)
    customers = pl.read_csv(customers_path)
    paid_sales = sales.filter(pl.col("status") == "paid")
    totals = (
        paid_sales.group_by("customer_id")
        .agg(
            total_spend=pl.col("net_revenue").sum(),
            paid_orders=pl.col("order_id").n_unique()
        )
    )
    result = customers.join(totals, on="customer_id", how="left")
    result = result.with_columns(
        pl.col("total_spend").fill_null(0.0).round(2),
        pl.col("paid_orders").fill_null(0).cast(pl.Int64)
    )
    result = result.with_columns(
        segment=pl.when(pl.col("total_spend") >= 250).then(pl.lit("vip")).otherwise(pl.lit("standard"))
    )
    return result.sort(["segment", "total_spend", "customer_id"], descending=[True, True, False])


def monthly_product_matrix(path: str | Path):
    df = load_sales(path)
    paid = df.filter(pl.col("status") == "paid").with_columns(
        pl.col("order_date").dt.strftime("%Y-%m").alias("month")
    )
    matrix = paid.pivot(values="net_revenue", index="month", columns="product", aggregate_function="sum").fill_null(0.0)
    product_columns = [col for col in matrix.columns if col != "month"]
    return matrix.with_columns(
        [pl.col(c).round(2) for c in product_columns]
    ).sort("month")


def latest_order_per_customer(path: str | Path):
    df = load_sales(path)
    latest = df.sort(["order_date", "order_id"], descending=[True, True]).unique(subset=["customer_id"], keep="first")
    return latest.select(
        ["customer_id", "order_id", "status", "order_date", "net_revenue"]
    ).sort("customer_id")


def invalid_sales_rows(path: str | Path):
    df = load_sales(path)
    return df.filter(
        pl.col("order_date").is_null()
        | (pl.col("quantity") <= 0)
        | (pl.col("unit_price") < 0)
        | ~pl.col("status").is_in(["paid", "pending", "cancelled"])
    ).select(["order_id", "customer_id", "status", "quantity", "unit_price"])
