import pandas as pd


def get_paid_orders(path):
    df = pd.read_csv(path)
    df = df[df["status"] == "paid"]
    return df[["customer_id", "total"]].sort_values("total")


def get_pending_orders(path):
    df = pd.read_csv(path)
    df = df[df["status"] == "pending"]
    return df[["order_id", "customer_id"]].sort_values("order_id")


def get_cancelled_orders(path):
    df = pd.read_csv(path)
    df = df[df["status"] == "cancelled"]
    return df[["order_id", "total"]].sort_values("total")


def get_paid_orders_for_north_region(path):
    df = pd.read_csv(path)
    df = df[df["status"] == "paid"]
    df = df[df["region"] == "north"]
    return df[["order_id", "customer_id", "total"]].sort_values("customer_id")


def get_high_priority_orders(path):
    df = pd.read_csv(path)
    df = df[df["priority"] == "high"]
    return df[["order_id", "status", "region"]].sort_values("status")
