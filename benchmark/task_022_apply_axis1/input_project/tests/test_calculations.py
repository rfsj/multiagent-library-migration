from __future__ import annotations

from pathlib import Path

from hr.calculations import classify_risk, compute_total_compensation, effective_hourly_rate

ROWS = [
    "employee_id,salary,bonus,allowance,absences,performance_score,hours_worked",
    "E001,5000,500,200,3,8,160",
    "E002,4000,200,150,12,7,0",
    "E003,6000,800,300,6,4,170",
    "E004,3500,100,100,2,9,150",
]


def _csv(tmp_path: Path, rows=None) -> Path:
    p = tmp_path / "employees.csv"
    p.write_text("\n".join(rows or ROWS) + "\n", encoding="utf-8")
    return p


def _records(frame):
    if hasattr(frame, "to_dicts"):
        return frame.to_dicts()
    return frame.to_dict(orient="records")


def _columns(frame):
    return list(frame.columns)


def test_total_compensation_columns(tmp_path):
    assert _columns(compute_total_compensation(_csv(tmp_path))) == [
        "employee_id", "salary", "bonus", "allowance", "total"
    ]


def test_total_compensation_values(tmp_path):
    result = {r["employee_id"]: r["total"] for r in _records(compute_total_compensation(_csv(tmp_path)))}
    assert result["E001"] == 5700
    assert result["E002"] == 4350
    assert result["E003"] == 7100
    assert result["E004"] == 3700


def test_total_compensation_row_count(tmp_path):
    assert len(_records(compute_total_compensation(_csv(tmp_path)))) == 4


def test_classify_risk_columns(tmp_path):
    assert _columns(classify_risk(_csv(tmp_path))) == [
        "employee_id", "absences", "performance_score", "risk"
    ]


def test_classify_risk_high_absences(tmp_path):
    result = {r["employee_id"]: r["risk"] for r in _records(classify_risk(_csv(tmp_path)))}
    assert result["E002"] == "high"


def test_classify_risk_medium(tmp_path):
    result = {r["employee_id"]: r["risk"] for r in _records(classify_risk(_csv(tmp_path)))}
    assert result["E003"] == "medium"


def test_classify_risk_low(tmp_path):
    result = {r["employee_id"]: r["risk"] for r in _records(classify_risk(_csv(tmp_path)))}
    assert result["E001"] == "low"
    assert result["E004"] == "low"


def test_classify_risk_row_count(tmp_path):
    assert len(_records(classify_risk(_csv(tmp_path)))) == 4


def test_effective_rate_columns(tmp_path):
    assert _columns(effective_hourly_rate(_csv(tmp_path))) == [
        "employee_id", "salary", "hours_worked", "hourly_rate"
    ]


def test_effective_rate_normal(tmp_path):
    result = {r["employee_id"]: r["hourly_rate"] for r in _records(effective_hourly_rate(_csv(tmp_path)))}
    assert result["E001"] == 31.25
    assert result["E003"] == round(6000 / 170, 2)
    assert result["E004"] == round(3500 / 150, 2)


def test_effective_rate_zero_hours(tmp_path):
    result = {r["employee_id"]: r["hourly_rate"] for r in _records(effective_hourly_rate(_csv(tmp_path)))}
    assert result["E002"] == 0.0
