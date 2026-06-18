from __future__ import annotations

from pathlib import Path

import polars as pl

from analytics.loaders import load_customers, paid_orders


def revenue_by_region(path: str | Path):
    paid = paid_orders(path)
    result = (
        paid.group_by("region")
        .agg(
            pl.col("net_revenue").sum().alias("total_revenue"),
            pl.col("order_id").n_unique().alias("orders"),
            pl.col("net_revenue").mean().alias("average_order_value"),
        )
        .with_columns(
            pl.col("total_revenue").round(2),
            pl.col("average_order_value").round(2),
        )
        .sort(["total_revenue", "region"], descending=[True, False])
    )
    return result


def customer_lifetime_value(orders_path: str | Path, customers_path: str | Path):
    customers = load_customers(customers_path)
    paid = paid_orders(orders_path)
    totals = (
        paid.group_by("customer_id")
        .agg(
            pl.col("net_revenue").sum().alias("total_spend"),
            pl.col("order_id").n_unique().alias("paid_orders"),
        )
    )
    result = customers.join(totals, on="customer_id", how="left")
    result = result.with_columns(
        pl.col("total_spend").fill_null(0.0).round(2),
        pl.col("paid_orders").fill_null(0).cast(pl.Int64),
    )
    result = result.with_columns(
        pl.when(pl.col("total_spend") >= 250)
        .then(pl.lit("vip"))
        .otherwise(pl.lit("standard"))
        .alias("segment")
    )
    return result.sort(
        ["segment", "total_spend", "customer_id"],
        descending=[True, True, False],
    )


def monthly_product_matrix(path: str | Path):
    paid = paid_orders(path)
    paid = paid.with_columns(pl.col("order_date").dt.strftime("%Y-%m").alias("month"))
    matrix = (
        paid.pivot(
            values="net_revenue",
            index="month",
            columns="product",
            aggregate_function="sum",
        )
        .fill_null(0.0)
    )
    product_columns = [column for column in matrix.columns if column != "month"]
    matrix = matrix.with_columns(
        [pl.col(col).round(2) for col in product_columns]
    )
    return matrix.sort("month")