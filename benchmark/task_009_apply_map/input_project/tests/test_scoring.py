from __future__ import annotations

from pathlib import Path

from employees.scoring import apply_bonus, assign_grade, map_department_name

ROWS = [
    "employee_id,department,score,salary",
    "E001,ENG,95,80000",
    "E002,HR,72,55000",
    "E003,MKT,85,65000",
    "E004,FIN,58,70000",
    "E005,ENG,60,90000",
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


def test_assign_grade_columns(tmp_path):
    assert _columns(assign_grade(_csv(tmp_path))) == ["employee_id", "score", "grade"]


def test_assign_grade_a(tmp_path):
    grades = {
        r["employee_id"]: r["grade"] for r in _records(assign_grade(_csv(tmp_path)))
    }
    assert grades["E001"] == "A"


def test_assign_grade_b(tmp_path):
    grades = {
        r["employee_id"]: r["grade"] for r in _records(assign_grade(_csv(tmp_path)))
    }
    assert grades["E003"] == "B"


def test_assign_grade_c(tmp_path):
    grades = {
        r["employee_id"]: r["grade"] for r in _records(assign_grade(_csv(tmp_path)))
    }
    assert grades["E002"] == "C"
    assert grades["E005"] == "C"


def test_assign_grade_d(tmp_path):
    grades = {
        r["employee_id"]: r["grade"] for r in _records(assign_grade(_csv(tmp_path)))
    }
    assert grades["E004"] == "D"


def test_assign_grade_sorted(tmp_path):
    ids = [r["employee_id"] for r in _records(assign_grade(_csv(tmp_path)))]
    assert ids == sorted(ids)


def test_map_department_name_columns(tmp_path):
    assert _columns(map_department_name(_csv(tmp_path))) == [
        "employee_id",
        "department",
        "department_name",
    ]


def test_map_department_name_values(tmp_path):
    result = {
        r["employee_id"]: r["department_name"]
        for r in _records(map_department_name(_csv(tmp_path)))
    }
    assert result["E001"] == "Engineering"
    assert result["E002"] == "Human Resources"
    assert result["E003"] == "Marketing"
    assert result["E004"] == "Finance"


def test_apply_bonus_columns(tmp_path):
    assert _columns(apply_bonus(_csv(tmp_path))) == ["employee_id", "salary", "bonus"]


def test_apply_bonus_default_rate(tmp_path):
    result = {
        r["employee_id"]: r["bonus"] for r in _records(apply_bonus(_csv(tmp_path)))
    }
    assert result["E001"] == 8000.0
    assert result["E002"] == 5500.0
    assert result["E005"] == 9000.0


def test_apply_bonus_custom_rate(tmp_path):
    result = {
        r["employee_id"]: r["bonus"]
        for r in _records(apply_bonus(_csv(tmp_path), rate=0.05))
    }
    assert result["E001"] == 4000.0
    assert result["E002"] == 2750.0
