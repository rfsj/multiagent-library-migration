import pandas as pd


def get_paid_orders(path):
    df = pd.read_csv(path)
    df = df[df["status"] == "paid"]
    return df[["customer_id", "total"]].sort_values("total")
