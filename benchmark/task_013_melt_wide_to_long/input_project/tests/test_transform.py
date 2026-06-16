from __future__ import annotations

from pathlib import Path

from surveys.transform import (
    group_average_by_question,
    question_score_range,
    wide_to_long,
)

ROWS = [
    "respondent_id,group,q1_score,q2_score,q3_score",
    "R1,A,8,7,9",
    "R2,A,6,8,7",
    "R3,B,9,6,8",
    "R4,B,7,9,6",
]


def _csv(tmp_path: Path, rows=None) -> Path:
    p = tmp_path / "survey.csv"
    p.write_text("\n".join(rows or ROWS) + "\n", encoding="utf-8")
    return p


def _records(frame):
    if hasattr(frame, "to_dicts"):
        return frame.to_dicts()
    return frame.to_dict(orient="records")


def _columns(frame):
    return list(frame.columns)


def test_wide_to_long_columns(tmp_path):
    assert _columns(wide_to_long(_csv(tmp_path))) == [
        "respondent_id",
        "question",
        "score",
    ]


def test_wide_to_long_row_count(tmp_path):
    assert len(_records(wide_to_long(_csv(tmp_path)))) == 12


def test_wide_to_long_r1_values(tmp_path):
    result = _records(wide_to_long(_csv(tmp_path)))
    r1 = {r["question"]: r["score"] for r in result if r["respondent_id"] == "R1"}
    assert r1["q1_score"] == 8
    assert r1["q2_score"] == 7
    assert r1["q3_score"] == 9


def test_wide_to_long_sorted(tmp_path):
    result = _records(wide_to_long(_csv(tmp_path)))
    pairs = [(r["respondent_id"], r["question"]) for r in result]
    assert pairs == sorted(pairs)


def test_group_average_columns(tmp_path):
    assert _columns(group_average_by_question(_csv(tmp_path))) == [
        "group",
        "question",
        "score",
    ]


def test_group_average_values(tmp_path):
    result = {
        (r["group"], r["question"]): r["score"]
        for r in _records(group_average_by_question(_csv(tmp_path)))
    }
    assert result[("A", "q1_score")] == 7.0
    assert result[("A", "q2_score")] == 7.5
    assert result[("B", "q3_score")] == 7.0


def test_group_average_sorted(tmp_path):
    result = _records(group_average_by_question(_csv(tmp_path)))
    pairs = [(r["group"], r["question"]) for r in result]
    assert pairs == sorted(pairs)


def test_question_score_range_columns(tmp_path):
    assert _columns(question_score_range(_csv(tmp_path))) == [
        "question",
        "min_score",
        "max_score",
    ]


def test_question_score_range_values(tmp_path):
    result = {r["question"]: r for r in _records(question_score_range(_csv(tmp_path)))}
    assert result["q1_score"]["min_score"] == 6
    assert result["q1_score"]["max_score"] == 9
    assert result["q2_score"]["min_score"] == 6
    assert result["q2_score"]["max_score"] == 9
    assert result["q3_score"]["min_score"] == 6
    assert result["q3_score"]["max_score"] == 9


def test_question_score_range_sorted(tmp_path):
    questions = [r["question"] for r in _records(question_score_range(_csv(tmp_path)))]
    assert questions == sorted(questions)
