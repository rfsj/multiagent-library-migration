from __future__ import annotations

import pandas as pd


def assign_grade(path):
    df = pd.read_csv(path)
    df["grade"] = df["score"].apply(
        lambda x: "A" if x >= 90 else ("B" if x >= 75 else ("C" if x >= 60 else "D"))
    )
    return (
        df[["employee_id", "score", "grade"]]
        .sort_values("employee_id")
        .reset_index(drop=True)
    )


def map_department_name(path):
    dept_map = {
        "HR": "Human Resources",
        "ENG": "Engineering",
        "MKT": "Marketing",
        "FIN": "Finance",
    }
    df = pd.read_csv(path)
    df["department_name"] = df["department"].map(dept_map)
    return (
        df[["employee_id", "department", "department_name"]]
        .sort_values("employee_id")
        .reset_index(drop=True)
    )


def apply_bonus(path, rate=0.1):
    df = pd.read_csv(path)
    df["bonus"] = df["salary"].apply(lambda x: round(x * rate, 2))
    return (
        df[["employee_id", "salary", "bonus"]]
        .sort_values("employee_id")
        .reset_index(drop=True)
    )
