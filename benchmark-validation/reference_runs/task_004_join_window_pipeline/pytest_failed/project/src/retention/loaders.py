from __future__ import annotations

from pathlib import Path

import polars as pl


def load_events(path: str | Path):
    events = pl.read_csv(path)
    events = events.with_columns([
        pl.col("event_time").str.to_datetime(strict=False),
        pl.col("event_name").str.strip_chars().str.to_lowercase(),
        pl.col("channel").fill_null("unknown").str.strip_chars().str.to_lowercase(),
        pl.col("revenue").fill_null(0.0)
    ])
    events = events.with_columns(
        pl.col("event_name").eq("purchase").alias("is_purchase")
    )
    return events.sort(["user_id", "event_time", "event_id"])


def load_users(path: str | Path):
    users = pl.read_csv(path)
    users = users.with_columns([
        pl.col("signup_date").str.to_datetime(strict=False),
        pl.col("country").fill_null("unknown").str.to_uppercase(),
        pl.col("plan").fill_null("free").str.to_lowercase()
    ])
    return users.sort("user_id")


def valid_events(path: str | Path):
    events = load_events(path)
    allowed = ["signup", "page_view", "purchase", "cancel"]
    return events.filter(
        pl.col("event_time").is_not_null() & pl.col("event_name").is_in(allowed)
    )