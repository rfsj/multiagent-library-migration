from __future__ import annotations

import pandas as pd


def recent_interactions(contacts_path, interactions_path, days=30):
    contacts = pd.read_csv(contacts_path)
    interactions = pd.read_csv(interactions_path)
    interactions["ts"] = pd.to_datetime(interactions["timestamp"])
    cutoff = interactions["ts"].max() - pd.Timedelta(days=days)
    recent = interactions[interactions["ts"] >= cutoff]
    merged = recent.merge(
        contacts[["contact_id", "first_name", "last_name"]],
        on="contact_id",
        how="left",
    )
    merged["contact_name"] = (
        merged["first_name"].str.strip() + " " + merged["last_name"].str.strip()
    )
    return (
        merged[["interaction_id", "contact_name", "timestamp", "type"]]
        .sort_values("interaction_id")
        .reset_index(drop=True)
    )


def interaction_summary(interactions_path):
    df = pd.read_csv(interactions_path)
    df["month"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m")
    return (
        df.groupby(["month", "type"], as_index=False)
        .size()
        .rename(columns={"size": "count"})
        .sort_values(["month", "type"])
        .reset_index(drop=True)
    )
