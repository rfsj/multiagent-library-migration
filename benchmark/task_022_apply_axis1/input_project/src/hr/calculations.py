from __future__ import annotations

import pandas as pd


def compute_total_compensation(path):
    df = pd.read_csv(path)
    df["total"] = df.apply(
        lambda row: row["salary"] + row["bonus"] + row["allowance"], axis=1
    )
    return (
        df[["employee_id", "salary", "bonus", "allowance", "total"]]
        .sort_values("employee_id")
        .reset_index(drop=True)
    )


def classify_risk(path):
    def _risk(row):
        if row["absences"] > 10 or row["performance_score"] < 3:
            return "high"
        if row["absences"] > 5 or row["performance_score"] < 6:
            return "medium"
        return "low"

    df = pd.read_csv(path)
    df["risk"] = df.apply(_risk, axis=1)
    return (
        df[["employee_id", "absences", "performance_score", "risk"]]
        .sort_values("employee_id")
        .reset_index(drop=True)
    )


def effective_hourly_rate(path):
    df = pd.read_csv(path)
    df["hourly_rate"] = df.apply(
        lambda row: round(row["salary"] / row["hours_worked"], 2)
        if row["hours_worked"] > 0
        else 0.0,
        axis=1,
    )
    return (
        df[["employee_id", "salary", "hours_worked", "hourly_rate"]]
        .sort_values("employee_id")
        .reset_index(drop=True)
    )
