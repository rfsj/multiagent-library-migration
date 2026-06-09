from __future__ import annotations

import pandas as pd


def combine_survey_results(demographics_path, responses_path):
    demographics = pd.read_csv(demographics_path)
    responses = pd.read_csv(responses_path).drop(columns=["respondent_id"])
    combined = pd.concat([demographics, responses], axis=1)
    return combined.sort_values("respondent_id").reset_index(drop=True)


def cap_values(path, low, high):
    df = pd.read_csv(path)
    df["value"] = df["value"].where(df["value"] >= low, low)
    df["value"] = df["value"].where(df["value"] <= high, high)
    return df.sort_values("id").reset_index(drop=True)


def stack_datasets(path_a, path_b):
    df_a = pd.read_csv(path_a)
    df_b = pd.read_csv(path_b)
    return (
        pd.concat([df_a, df_b], axis=0, ignore_index=True)
        .sort_values(["period", "region"])
        .reset_index(drop=True)
    )
