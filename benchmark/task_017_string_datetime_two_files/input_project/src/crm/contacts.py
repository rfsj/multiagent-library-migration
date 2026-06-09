from __future__ import annotations

import pandas as pd


def normalize_contacts(path):
    df = pd.read_csv(path)
    df["email"] = df["email"].str.strip().str.lower()
    df["full_name"] = df["first_name"].str.strip() + " " + df["last_name"].str.strip()
    df["phone_clean"] = df["phone"].str.replace(r"[^0-9]", "", regex=True)
    return (
        df[["contact_id", "full_name", "email", "phone_clean"]]
        .sort_values("contact_id")
        .reset_index(drop=True)
    )


def find_by_domain(path, domain):
    df = pd.read_csv(path)
    df["email_lower"] = df["email"].str.strip().str.lower()
    mask = df["email_lower"].str.endswith("@" + domain)
    return (
        df[mask][["contact_id", "email"]]
        .sort_values("contact_id")
        .reset_index(drop=True)
    )
