from __future__ import annotations

import pandas as pd


def wide_to_long(path):
    df = pd.read_csv(path)
    melted = df.melt(
        id_vars=["respondent_id"],
        value_vars=["q1_score", "q2_score", "q3_score"],
        var_name="question",
        value_name="score",
    )
    return melted.sort_values(["respondent_id", "question"]).reset_index(drop=True)


def group_average_by_question(path):
    df = pd.read_csv(path)
    melted = df.melt(
        id_vars=["respondent_id", "group"],
        value_vars=["q1_score", "q2_score", "q3_score"],
        var_name="question",
        value_name="score",
    )
    return (
        melted.groupby(["group", "question"], as_index=False)["score"]
        .mean()
        .round(2)
        .sort_values(["group", "question"])
        .reset_index(drop=True)
    )


def question_score_range(path):
    df = pd.read_csv(path)
    melted = df.melt(
        id_vars=["respondent_id"],
        value_vars=["q1_score", "q2_score", "q3_score"],
        var_name="question",
        value_name="score",
    )
    return (
        melted.groupby("question", as_index=False)
        .agg(min_score=("score", "min"), max_score=("score", "max"))
        .sort_values("question")
        .reset_index(drop=True)
    )
