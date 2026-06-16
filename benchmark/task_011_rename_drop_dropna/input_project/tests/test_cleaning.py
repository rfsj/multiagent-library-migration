from __future__ import annotations

from pathlib import Path

from sales.cleaning import clean_for_reporting, remove_incomplete, standardize_columns

RENAME_ROWS = [
    "sale_id,cust_id,rev,qty",
    "1,C3,150.0,7",
    "2,C1,100.0,5",
    "3,C2,200.0,10",
]

INCOMPLETE_ROWS = [
    "sale_id,customer_id,revenue,quantity",
    "1,C1,100.0,5",
    "2,C2,,10",
    "3,C3,150.0,",
    "4,C4,200.0,8",
]

REPORTING_ROWS = [
    "sale_id,customer_id,revenue,internal_flag,notes",
    "1,C1,100.0,1,promo",
    "2,C2,0.0,0,none",
    "3,C3,150.0,1,bulk",
    "4,C4,-50.0,0,refund",
]


def _csv(tmp_path: Path, rows: list[str], name="data.csv") -> Path:
    p = tmp_path / name
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def _records(frame):
    if hasattr(frame, "to_dicts"):
        return frame.to_dicts()
    return frame.to_dict(orient="records")


def _columns(frame):
    return list(frame.columns)


def test_standardize_columns_renames(tmp_path):
    result = standardize_columns(_csv(tmp_path, RENAME_ROWS, "rename.csv"))
    assert _columns(result) == ["customer_id", "revenue", "quantity"]


def test_standardize_columns_sorted(tmp_path):
    result = _records(standardize_columns(_csv(tmp_path, RENAME_ROWS, "rename.csv")))
    ids = [r["customer_id"] for r in result]
    assert ids == sorted(ids)


def test_standardize_columns_values(tmp_path):
    result = {
        r["customer_id"]: r["revenue"]
        for r in _records(
            standardize_columns(_csv(tmp_path, RENAME_ROWS, "rename.csv"))
        )
    }
    assert result["C1"] == 100.0
    assert result["C2"] == 200.0
    assert result["C3"] == 150.0


def test_remove_incomplete_drops_null_revenue(tmp_path):
    result = _records(
        remove_incomplete(_csv(tmp_path, INCOMPLETE_ROWS, "incomplete.csv"))
    )
    sale_ids = {r["sale_id"] for r in result}
    assert 2 not in sale_ids


def test_remove_incomplete_drops_null_quantity(tmp_path):
    result = _records(
        remove_incomplete(_csv(tmp_path, INCOMPLETE_ROWS, "incomplete.csv"))
    )
    sale_ids = {r["sale_id"] for r in result}
    assert 3 not in sale_ids


def test_remove_incomplete_keeps_complete_rows(tmp_path):
    result = _records(
        remove_incomplete(_csv(tmp_path, INCOMPLETE_ROWS, "incomplete.csv"))
    )
    sale_ids = {r["sale_id"] for r in result}
    assert 1 in sale_ids
    assert 4 in sale_ids


def test_remove_incomplete_sorted(tmp_path):
    result = _records(
        remove_incomplete(_csv(tmp_path, INCOMPLETE_ROWS, "incomplete.csv"))
    )
    ids = [r["sale_id"] for r in result]
    assert ids == sorted(ids)


def test_clean_for_reporting_removes_zero_revenue(tmp_path):
    result = _records(
        clean_for_reporting(_csv(tmp_path, REPORTING_ROWS, "reporting.csv"))
    )
    sale_ids = {r["sale_id"] for r in result}
    assert 2 not in sale_ids


def test_clean_for_reporting_removes_negative_revenue(tmp_path):
    result = _records(
        clean_for_reporting(_csv(tmp_path, REPORTING_ROWS, "reporting.csv"))
    )
    sale_ids = {r["sale_id"] for r in result}
    assert 4 not in sale_ids


def test_clean_for_reporting_keeps_positive_rows(tmp_path):
    result = _records(
        clean_for_reporting(_csv(tmp_path, REPORTING_ROWS, "reporting.csv"))
    )
    sale_ids = {r["sale_id"] for r in result}
    assert 1 in sale_ids
    assert 3 in sale_ids


def test_clean_for_reporting_drops_columns(tmp_path):
    result = clean_for_reporting(_csv(tmp_path, REPORTING_ROWS, "reporting.csv"))
    cols = _columns(result)
    assert "internal_flag" not in cols
    assert "notes" not in cols
    assert "sale_id" in cols
    assert "revenue" in cols
