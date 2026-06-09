from __future__ import annotations

from pathlib import Path

from pipeline.combine import cap_values, combine_survey_results, stack_datasets

DEMO_ROWS = [
    "respondent_id,age,city",
    "R1,25,NY",
    "R2,34,LA",
    "R3,28,Chicago",
]

RESPONSE_ROWS = [
    "respondent_id,q1,q2,q3",
    "R1,8,7,9",
    "R2,6,8,7",
    "R3,9,6,8",
]

CAP_ROWS = [
    "id,value",
    "1,5",
    "2,-10",
    "3,150",
    "4,50",
    "5,0",
]

PERIOD_A_ROWS = [
    "period,region,revenue",
    "Q1,north,100",
    "Q1,south,200",
]

PERIOD_B_ROWS = [
    "period,region,revenue",
    "Q2,north,150",
    "Q2,south,180",
]


def _csv(tmp_path: Path, rows: list[str], name: str) -> Path:
    p = tmp_path / name
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def _records(frame):
    if hasattr(frame, "to_dicts"):
        return frame.to_dicts()
    return frame.to_dict(orient="records")


def _columns(frame):
    return list(frame.columns)


def test_combine_survey_columns(tmp_path):
    d = _csv(tmp_path, DEMO_ROWS, "demo.csv")
    r = _csv(tmp_path, RESPONSE_ROWS, "resp.csv")
    cols = _columns(combine_survey_results(d, r))
    assert cols == ["respondent_id", "age", "city", "q1", "q2", "q3"]


def test_combine_survey_row_count(tmp_path):
    d = _csv(tmp_path, DEMO_ROWS, "demo.csv")
    r = _csv(tmp_path, RESPONSE_ROWS, "resp.csv")
    assert len(_records(combine_survey_results(d, r))) == 3


def test_combine_survey_values(tmp_path):
    d = _csv(tmp_path, DEMO_ROWS, "demo.csv")
    r = _csv(tmp_path, RESPONSE_ROWS, "resp.csv")
    result = {row["respondent_id"]: row for row in _records(combine_survey_results(d, r))}
    assert result["R1"]["age"] == 25
    assert result["R1"]["q1"] == 8
    assert result["R2"]["city"] == "LA"
    assert result["R2"]["q2"] == 8


def test_cap_values_columns(tmp_path):
    p = _csv(tmp_path, CAP_ROWS, "cap.csv")
    assert _columns(cap_values(p, 0, 100)) == ["id", "value"]


def test_cap_values_row_count_preserved(tmp_path):
    p = _csv(tmp_path, CAP_ROWS, "cap.csv")
    assert len(_records(cap_values(p, 0, 100))) == 5


def test_cap_values_clamps_below(tmp_path):
    p = _csv(tmp_path, CAP_ROWS, "cap.csv")
    result = {r["id"]: r["value"] for r in _records(cap_values(p, 0, 100))}
    assert result[2] == 0


def test_cap_values_clamps_above(tmp_path):
    p = _csv(tmp_path, CAP_ROWS, "cap.csv")
    result = {r["id"]: r["value"] for r in _records(cap_values(p, 0, 100))}
    assert result[3] == 100


def test_cap_values_keeps_within_range(tmp_path):
    p = _csv(tmp_path, CAP_ROWS, "cap.csv")
    result = {r["id"]: r["value"] for r in _records(cap_values(p, 0, 100))}
    assert result[1] == 5
    assert result[4] == 50
    assert result[5] == 0


def test_stack_datasets_columns(tmp_path):
    a = _csv(tmp_path, PERIOD_A_ROWS, "q1.csv")
    b = _csv(tmp_path, PERIOD_B_ROWS, "q2.csv")
    assert _columns(stack_datasets(a, b)) == ["period", "region", "revenue"]


def test_stack_datasets_row_count(tmp_path):
    a = _csv(tmp_path, PERIOD_A_ROWS, "q1.csv")
    b = _csv(tmp_path, PERIOD_B_ROWS, "q2.csv")
    assert len(_records(stack_datasets(a, b))) == 4


def test_stack_datasets_contains_all_periods(tmp_path):
    a = _csv(tmp_path, PERIOD_A_ROWS, "q1.csv")
    b = _csv(tmp_path, PERIOD_B_ROWS, "q2.csv")
    periods = {r["period"] for r in _records(stack_datasets(a, b))}
    assert periods == {"Q1", "Q2"}


def test_stack_datasets_sorted(tmp_path):
    a = _csv(tmp_path, PERIOD_A_ROWS, "q1.csv")
    b = _csv(tmp_path, PERIOD_B_ROWS, "q2.csv")
    result = _records(stack_datasets(a, b))
    pairs = [(r["period"], r["region"]) for r in result]
    assert pairs == sorted(pairs)
