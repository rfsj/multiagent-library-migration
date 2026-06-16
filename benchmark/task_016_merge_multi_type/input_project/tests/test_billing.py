from __future__ import annotations

from pathlib import Path

from billing.customers import (
    active_customers,
    customers_with_invoices,
    customers_without_invoices,
)
from billing.invoices import all_billing_pairs, invoice_totals_by_plan

CUSTOMER_ROWS = [
    "customer_id,name,status,plan",
    "C1,Alice,active,premium",
    "C2,Bob,active,basic",
    "C3,Carol,inactive,basic",
    "C4,Dave,active,premium",
    "C5,Eve,active,basic",
]

INVOICE_ROWS = [
    "invoice_id,customer_id,amount",
    "I1,C1,100.0",
    "I2,C2,50.0",
    "I3,C1,80.0",
    "I4,C4,120.0",
    "I5,C6,90.0",
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


def _paths(tmp_path):
    c = _csv(tmp_path, CUSTOMER_ROWS, "customers.csv")
    i = _csv(tmp_path, INVOICE_ROWS, "invoices.csv")
    return c, i


def test_active_customers_columns(tmp_path):
    c, _ = _paths(tmp_path)
    assert _columns(active_customers(c)) == ["customer_id", "name", "plan"]


def test_active_customers_excludes_inactive(tmp_path):
    c, _ = _paths(tmp_path)
    ids = {r["customer_id"] for r in _records(active_customers(c))}
    assert "C3" not in ids


def test_active_customers_includes_active(tmp_path):
    c, _ = _paths(tmp_path)
    ids = {r["customer_id"] for r in _records(active_customers(c))}
    assert ids == {"C1", "C2", "C4", "C5"}


def test_customers_with_invoices_columns(tmp_path):
    c, i = _paths(tmp_path)
    assert _columns(customers_with_invoices(c, i)) == [
        "customer_id",
        "name",
        "invoice_id",
        "amount",
    ]


def test_customers_with_invoices_inner_join(tmp_path):
    c, i = _paths(tmp_path)
    ids = {r["customer_id"] for r in _records(customers_with_invoices(c, i))}
    assert ids == {"C1", "C2", "C4"}
    assert "C5" not in ids
    assert "C6" not in ids


def test_customers_with_invoices_multiple_rows(tmp_path):
    c, i = _paths(tmp_path)
    result = [
        r for r in _records(customers_with_invoices(c, i)) if r["customer_id"] == "C1"
    ]
    assert len(result) == 2


def test_customers_without_invoices_columns(tmp_path):
    c, i = _paths(tmp_path)
    assert _columns(customers_without_invoices(c, i)) == ["customer_id", "name"]


def test_customers_without_invoices_anti_join(tmp_path):
    c, i = _paths(tmp_path)
    ids = {r["customer_id"] for r in _records(customers_without_invoices(c, i))}
    assert ids == {"C3", "C5"}


def test_invoice_totals_by_plan_columns(tmp_path):
    c, i = _paths(tmp_path)
    assert _columns(invoice_totals_by_plan(c, i)) == ["plan", "total_amount"]


def test_invoice_totals_by_plan_values(tmp_path):
    c, i = _paths(tmp_path)
    result = {
        r["plan"]: r["total_amount"] for r in _records(invoice_totals_by_plan(c, i))
    }
    assert result["premium"] == 300.0
    assert result["basic"] == 50.0


def test_all_billing_pairs_includes_unmatched(tmp_path):
    c, i = _paths(tmp_path)
    ids = {r["customer_id"] for r in _records(all_billing_pairs(c, i))}
    assert "C5" in ids
    assert "C6" in ids
