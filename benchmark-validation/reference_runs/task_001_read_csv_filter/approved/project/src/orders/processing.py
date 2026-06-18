import polars as pl


def get_paid_orders(path):
    df = pl.read_csv(path)
    df = df.filter(pl.col("status") == "paid")
    return df.select(["customer_id", "total"]).sort("total")


def get_pending_orders(path):
    df = pl.read_csv(path)
    df = df.filter(pl.col("status") == "pending")
    return df.select(["order_id", "customer_id"]).sort("order_id")


def get_cancelled_orders(path):
    df = pl.read_csv(path)
    df = df.filter(pl.col("status") == "cancelled")
    return df.select(["order_id", "total"]).sort("total")


def get_paid_orders_for_north_region(path):
    df = pl.read_csv(path)
    df = df.filter(pl.col("status") == "paid")
    df = df.filter(pl.col("region") == "north")
    return df.select(["order_id", "customer_id", "total"]).sort("customer_id")


def get_high_priority_orders(path):
    df = pl.read_csv(path)
    df = df.filter(pl.col("priority") == "high")
    return df.select(["order_id", "status", "region"]).sort("status")
