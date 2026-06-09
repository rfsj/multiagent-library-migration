from __future__ import annotations

from pathlib import Path

from reports.aggregations import invalid_rows, revenue_by_category, top_categories

ROWS = [
    "order_id,status,category,amount",
    "1,paid,electronics,100.0",
    "2,paid,electronics,50.0",
    "3,pending,tools,30.0",
    "4,cancelled,tools,20.0",
    "5,paid,garden,80.0",
    "6,refunded,electronics,10.0",
    "7,paid,garden,40.0",
    "8,paid,,60.0",
    "9,pending,tools,",
]


def _csv(tmp_path: Path, rows=None) -> Path:
    p = tmp_path / "orders.csv"
    p.write_text("\n".join(rows or ROWS) + "\n", encoding="utf-8")
    return p


def _records(frame):
    if hasattr(frame, "to_dicts"):
        return frame.to_dicts()
    return frame.to_dict(orient="records")


def _cols(frame):
    return list(frame.columns)


def test_revenue_by_category_sums_only_paid(tmp_path):
    result = revenue_by_category(_csv(tmp_path))
    records = _records(result)
    categories = {r["category"] for r in records}
    # only paid orders → tools (cancelled) and refunded should not appear
    assert "tools" not in categories
    assert categories == {"electronics", "garden"}


def test_revenue_by_category_correct_totals(tmp_path):
    result = revenue_by_category(_csv(tmp_path))
    records = _records(result)
    by_cat = {r["category"]: r["total_revenue"] for r in records}
    assert by_cat["electronics"] == 150.0
    assert by_cat["garden"] == 120.0


def test_revenue_by_category_sorted_descending(tmp_path):
    result = revenue_by_category(_csv(tmp_path))
    revenues = [r["total_revenue"] for r in _records(result)]
    assert revenues == sorted(revenues, reverse=True)


def test_revenue_by_category_columns(tmp_path):
    assert _cols(revenue_by_category(_csv(tmp_path))) == [
        "category", "total_revenue", "order_count"
    ]


def test_invalid_rows_detects_unknown_status(tmp_path):
    result = invalid_rows(_csv(tmp_path))
    ids = {r["order_id"] for r in _records(result)}
    assert 6 in ids  # refunded = unknown


def test_invalid_rows_detects_null_amount(tmp_path):
    result = invalid_rows(_csv(tmp_path))
    ids = {r["order_id"] for r in _records(result)}
    assert 9 in ids  # null amount


def test_invalid_rows_detects_null_category(tmp_path):
    result = invalid_rows(_csv(tmp_path))
    ids = {r["order_id"] for r in _records(result)}
    assert 8 in ids  # null category


def test_invalid_rows_sorted_by_order_id(tmp_path):
    result = invalid_rows(_csv(tmp_path))
    ids = [r["order_id"] for r in _records(result)]
    assert ids == sorted(ids)


def test_invalid_rows_columns(tmp_path):
    assert _cols(invalid_rows(_csv(tmp_path))) == ["order_id", "status", "amount"]


def test_top_categories_includes_paid_and_pending(tmp_path):
    result = top_categories(_csv(tmp_path), n=2)
    records = _records(result)
    # tools has 30.0 (pending) + 20.0 (cancelled — excluded) → 30.0
    # electronics has 100+50 (paid) + 10 (refunded — excluded) → 150
    # garden has 80+40 = 120
    categories = [r["category"] for r in records]
    assert "electronics" in categories
    assert "garden" in categories


def test_top_categories_n_limits_result(tmp_path):
    result = top_categories(_csv(tmp_path), n=1)
    assert len(_records(result)) == 1


def test_top_categories_columns(tmp_path):
    assert _cols(top_categories(_csv(tmp_path))) == ["category", "total"]
