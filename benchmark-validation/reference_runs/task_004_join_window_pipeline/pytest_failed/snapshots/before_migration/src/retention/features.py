from __future__ import annotations

from pathlib import Path

import pandas as pd

from retention.loaders import load_users, valid_events


def user_activity_summary(events_path: str | Path, users_path: str | Path):
    users = load_users(users_path)
    events = valid_events(events_path)
    activity = (
        events.groupby("user_id", as_index=False)
        .agg(
            first_seen=("event_time", "min"),
            last_seen=("event_time", "max"),
            sessions=("session_id", "nunique"),
            purchases=("is_purchase", "sum"),
            total_revenue=("revenue", "sum"),
        )
        .sort_values("user_id")
    )
    result = users.merge(activity, on="user_id", how="left")
    result["sessions"] = result["sessions"].fillna(0).astype(int)
    result["purchases"] = result["purchases"].fillna(0).astype(int)
    result["total_revenue"] = result["total_revenue"].fillna(0.0).round(2)
    result["days_active"] = (result["last_seen"] - result["signup_date"]).dt.days
    result["days_active"] = result["days_active"].fillna(0).astype(int)
    result["status"] = result["purchases"].apply(
        lambda value: "buyer" if value > 0 else "prospect"
    )
    return result[
        [
            "user_id",
            "country",
            "plan",
            "sessions",
            "purchases",
            "total_revenue",
            "days_active",
            "status",
        ]
    ].sort_values(["status", "total_revenue", "user_id"], ascending=[True, False, True])


def first_touch_revenue(events_path: str | Path):
    events = valid_events(events_path)
    first_touch = events.sort_values(["user_id", "event_time", "event_id"]).drop_duplicates(
        subset=["user_id"],
        keep="first",
    )
    revenue = (
        events[events["is_purchase"]]
        .groupby("user_id", as_index=False)
        .agg(total_revenue=("revenue", "sum"), purchases=("event_id", "nunique"))
    )
    result = first_touch[["user_id", "channel"]].merge(revenue, on="user_id", how="left")
    result["total_revenue"] = result["total_revenue"].fillna(0.0).round(2)
    result["purchases"] = result["purchases"].fillna(0).astype(int)
    return result.sort_values(["total_revenue", "user_id"], ascending=[False, True])


def weekly_cohort_retention(events_path: str | Path, users_path: str | Path):
    users = load_users(users_path)
    events = valid_events(events_path)
    events["event_day"] = events["event_time"].dt.floor("D")
    unique_active_days = events.drop_duplicates(subset=["user_id", "event_day"]).copy()
    joined = unique_active_days.merge(users[["user_id", "signup_date"]], on="user_id", how="inner")
    joined["cohort"] = joined["signup_date"].dt.strftime("%Y-%m")
    joined["days_since_signup"] = (joined["event_day"] - joined["signup_date"]).dt.days
    window = joined[
        (joined["days_since_signup"] >= 0)
        & (joined["days_since_signup"] <= 7)
        & joined["signup_date"].notna()
    ]
    matrix = pd.pivot_table(
        window,
        values="user_id",
        index="cohort",
        columns="days_since_signup",
        aggfunc="nunique",
        fill_value=0,
    )
    matrix = matrix.reset_index()
    day_columns = [column for column in matrix.columns if column != "cohort"]
    matrix[day_columns] = matrix[day_columns].astype(int)
    return matrix.sort_values("cohort").reset_index(drop=True)
