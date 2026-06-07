from __future__ import annotations

import pandas as pd


def search_by_keyword(path, keyword):
    df = pd.read_csv(path)
    mask = df["name"].str.contains(keyword, case=False, na=False)
    return df[mask][["id", "name", "category"]].sort_values("name")


def normalize_catalog(path):
    df = pd.read_csv(path)
    df["category"] = df["category"].str.strip().str.lower()
    df["name"] = df["name"].str.strip()
    return df[["id", "name", "category"]].sort_values(["category", "name"])


def items_starting_with(path, prefix):
    df = pd.read_csv(path)
    mask = df["name"].str.startswith(prefix, na=False)
    return df[mask][["id", "name"]].sort_values("id")


def uppercase_names(path):
    df = pd.read_csv(path)
    df["name"] = df["name"].str.upper()
    return df[["id", "name"]].sort_values("id")
