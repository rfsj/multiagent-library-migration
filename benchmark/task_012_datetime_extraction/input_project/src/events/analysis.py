from __future__ import annotations

import pandas as pd


def extract_time_features(path):
    df = pd.read_csv(path)
    df["ts"] = pd.to_datetime(df["timestamp"])
    df["year"] = df["ts"].dt.year
    df["month"] = df["ts"].dt.month
    df["day"] = df["ts"].dt.day
    df["hour"] = df["ts"].dt.hour
    return (
        df[["event_id", "year", "month", "day", "hour"]]
        .sort_values("event_id")
        .reset_index(drop=True)
    )


def events_per_date(path):
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d")
    result = df.groupby("date", as_index=False).size().rename(columns={"size": "count"})
    return result.sort_values("date").reset_index(drop=True)


def filter_by_hour_range(path, start_hour, end_hour):
    df = pd.read_csv(path)
    df["ts"] = pd.to_datetime(df["timestamp"])
    mask = (df["ts"].dt.hour >= start_hour) & (df["ts"].dt.hour < end_hour)
    return (
        df[mask][["event_id", "timestamp", "event_type"]]
        .sort_values("event_id")
        .reset_index(drop=True)
    )
