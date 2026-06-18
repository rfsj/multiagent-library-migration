from __future__ import annotations

from pathlib import Path

import polars as pl

from retention.loaders import load_users, valid_events


def user_activity_summary(events_path: str | Path, users_path: str | Path):
    users = load_users(users_path)
    events = valid_events(events_path)
    activity = (
        events.group_by("user_id")
        .agg(
            pl.col("event_time").min().alias("first_seen"),
            pl.col("event_time").max().alias("last_seen"),
            pl.col("session_id").n_unique().alias("sessions"),
            pl.col("is_purchase").sum().alias("purchases"),
            pl.col("revenue").sum().alias("total_revenue"),
        )
        .sort("user_id")
    )
    result = users.join(activity, on="user_id", how="left")
    result = result.with_columns([
        pl.col("sessions").fill_null(0).cast(pl.Int64),
        pl.col("purchases").fill_null(0).cast(pl.Int64),
        pl.col("total_revenue").fill_null(0.0).round(2),
        (pl.col("last_seen") - pl.col("signup_date")).dt.total_days().fill_null(0).cast(pl.Int64).alias("days_active"),
        pl.when(pl.col("purchases") > 0).then(pl.lit("buyer")).otherwise(pl.lit("prospect")).alias("status")
    ])
    return result.select([
        "user_id", "country", "plan", "sessions", "purchases", "total_revenue", "days_active", "status"
    ]).sort(["status", "total_revenue", "user_id"], descending=[False, True, False])


def first_touch_revenue(events_path: str | Path):
    events = valid_events(events_path)
    first_touch = events.sort(["user_id", "event_time", "event_id"]).unique(subset=["user_id"], keep="first")
    revenue = (
        events.filter(pl.col("is_purchase"))
        .group_by("user_id")
        .agg(
            pl.col("revenue").sum().alias("total_revenue"),
            pl.col("event_id").n_unique().alias("purchases")
        )
    )
    result = first_touch.select(["user_id", "channel"]).join(revenue, on="user_id", how="left")
    result = result.with_columns([
        pl.col("total_revenue").fill_null(0.0).round(2),
        pl.col("purchases").fill_null(0).cast(pl.Int64)
    ])
    return result.sort(["total_revenue", "user_id"], descending=[True, False])


def weekly_cohort_retention(events_path: str | Path, users_path: str | Path):
    users = load_users(users_path)
    events = valid_events(events_path)
    events = events.with_columns(pl.col("event_time").dt.truncate("1d").alias("event_day"))
    unique_active_days = events.unique(subset=["user_id", "event_day"])
    joined = unique_active_days.join(users.select(["user_id", "signup_date"]), on="user_id", how="inner")
    joined = joined.with_columns([
        pl.col("signup_date").dt.strftime("%Y-%m").alias("cohort"),
        (pl.col("event_day") - pl.col("signup_date")).dt.total_days().alias("days_since_signup")
    ])
    window = joined.filter(
        (pl.col("days_since_signup") >= 0) &
        (pl.col("days_since_signup") <= 7) &
        pl.col("signup_date").is_not_null()
    )
    matrix = window.pivot(index="cohort", columns="days_since_signup", values="user_id", aggregate_function="n_unique")
    matrix = matrix.fill_null(0)
    day_columns = [c for c in matrix.columns if c != "cohort"]
    matrix = matrix.with_columns([pl.col(c).cast(pl.Int64) for c in day_columns])
    return matrix.sort("cohort")