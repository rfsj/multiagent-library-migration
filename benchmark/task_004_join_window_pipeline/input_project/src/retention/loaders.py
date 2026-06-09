from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_events(path: str | Path):
    events = pd.read_csv(path)
    events["event_time"] = pd.to_datetime(events["event_time"], errors="coerce")
    events["event_name"] = events["event_name"].str.strip().str.lower()
    events["channel"] = events["channel"].fillna("unknown").str.strip().str.lower()
    events["revenue"] = events["revenue"].fillna(0.0)
    events["is_purchase"] = events["event_name"] == "purchase"
    return events.sort_values(["user_id", "event_time", "event_id"]).reset_index(drop=True)


def load_users(path: str | Path):
    users = pd.read_csv(path)
    users["signup_date"] = pd.to_datetime(users["signup_date"], errors="coerce")
    users["country"] = users["country"].fillna("unknown").str.upper()
    users["plan"] = users["plan"].fillna("free").str.lower()
    return users.sort_values("user_id").reset_index(drop=True)


def valid_events(path: str | Path):
    events = load_events(path)
    allowed = ["signup", "page_view", "purchase", "cancel"]
    return events[events["event_time"].notna() & events["event_name"].isin(allowed)].copy()
