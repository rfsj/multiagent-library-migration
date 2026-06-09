from __future__ import annotations

from pathlib import Path

from retail.aggregation import monthly_revenue_by_segment, rolling_revenue_trend
from retail.enrichment import enrich_transactions, flag_high_value
from retail.ingestion import load_customers, load_transactions
from retail.reporting import category_sales_report, segment_category_pivot

TXN_ROWS = [
    "txn_id,customer_id,product_id,amt,transaction_date",
    "T1,CU1,PR1,50.0,2024-01-10",
    "T2,CU2,PR2,120.0,2024-01-15",
    "T3,CU1,PR3,80.0,2024-01-20",
    "T4,CU3,PR1,200.0,2024-02-05",
    "T5,CU2,PR3,60.0,2024-02-10",
    "T6,CU1,PR2,150.0,2024-02-15",
    "T7,CU3,PR2,,2024-02-20",
]

CUSTOMER_ROWS = [
    "customer_id,email,age,signup_date,segment",
    "CU1, ALICE@EXAMPLE.COM ,28,2023-01-01,premium",
    "CU2,bob@test.org,45,2023-06-15,standard",
    "CU3, carol@example.com ,62,2022-09-01,premium",
]

PRODUCT_ROWS = [
    "product_id,category,brand,cost_price",
    "PR1, electronics , Acme ,35.0",
    "PR2,furniture,Bolt,80.0",
    "PR3, electronics ,Acme,40.0",
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
    t = _csv(tmp_path, TXN_ROWS, "transactions.csv")
    c = _csv(tmp_path, CUSTOMER_ROWS, "customers.csv")
    p = _csv(tmp_path, PRODUCT_ROWS, "products.csv")
    return t, c, p


def test_load_transactions_renames_columns(tmp_path):
    t, _, _ = _paths(tmp_path)
    cols = _columns(load_transactions(t))
    assert "transaction_id" in cols
    assert "amount" in cols
    assert "txn_id" not in cols
    assert "amt" not in cols


def test_load_transactions_fills_null_amount(tmp_path):
    t, _, _ = _paths(tmp_path)
    result = {r["transaction_id"]: r["amount"] for r in _records(load_transactions(t))}
    assert result["T7"] == 0.0


def test_load_transactions_row_count(tmp_path):
    t, _, _ = _paths(tmp_path)
    assert len(_records(load_transactions(t))) == 7


def test_load_customers_columns(tmp_path):
    _, c, _ = _paths(tmp_path)
    assert _columns(load_customers(c)) == [
        "customer_id", "email", "age_group", "signup_date", "segment"
    ]


def test_load_customers_email_normalized(tmp_path):
    _, c, _ = _paths(tmp_path)
    result = {r["customer_id"]: r["email"] for r in _records(load_customers(c))}
    assert result["CU1"] == "alice@example.com"
    assert result["CU3"] == "carol@example.com"


def test_load_customers_age_group(tmp_path):
    _, c, _ = _paths(tmp_path)
    result = {r["customer_id"]: r["age_group"] for r in _records(load_customers(c))}
    assert result["CU1"] == "young"
    assert result["CU2"] == "middle"
    assert result["CU3"] == "senior"


def test_enrich_transactions_columns(tmp_path):
    t, _, p = _paths(tmp_path)
    assert _columns(enrich_transactions(t, p)) == [
        "transaction_id", "customer_id", "amount", "category_clean", "brand_clean", "month"
    ]


def test_enrich_transactions_category_stripped(tmp_path):
    t, _, p = _paths(tmp_path)
    result = {r["transaction_id"]: r for r in _records(enrich_transactions(t, p))}
    assert result["T1"]["category_clean"] == "electronics"
    assert result["T2"]["category_clean"] == "furniture"


def test_enrich_transactions_brand_stripped(tmp_path):
    t, _, p = _paths(tmp_path)
    result = {r["transaction_id"]: r for r in _records(enrich_transactions(t, p))}
    assert result["T1"]["brand_clean"] == "Acme"


def test_enrich_transactions_month(tmp_path):
    t, _, p = _paths(tmp_path)
    result = {r["transaction_id"]: r["month"] for r in _records(enrich_transactions(t, p))}
    assert result["T1"] == "2024-01"
    assert result["T4"] == "2024-02"


def test_flag_high_value_columns(tmp_path):
    t, _, _ = _paths(tmp_path)
    assert _columns(flag_high_value(t)) == [
        "transaction_id", "customer_id", "amount", "is_high_value"
    ]


def test_flag_high_value_true(tmp_path):
    t, _, _ = _paths(tmp_path)
    result = {r["transaction_id"]: r["is_high_value"] for r in _records(flag_high_value(t))}
    assert result["T2"] is True
    assert result["T4"] is True
    assert result["T6"] is True


def test_flag_high_value_false(tmp_path):
    t, _, _ = _paths(tmp_path)
    result = {r["transaction_id"]: r["is_high_value"] for r in _records(flag_high_value(t))}
    assert result["T1"] is False
    assert result["T7"] is False


def test_monthly_revenue_by_segment_columns(tmp_path):
    t, c, _ = _paths(tmp_path)
    assert _columns(monthly_revenue_by_segment(t, c)) == ["segment", "month", "amount"]


def test_monthly_revenue_premium_jan(tmp_path):
    t, c, _ = _paths(tmp_path)
    result = {(r["segment"], r["month"]): r["amount"] for r in _records(monthly_revenue_by_segment(t, c))}
    assert result[("premium", "2024-01")] == 130.0


def test_monthly_revenue_premium_feb(tmp_path):
    t, c, _ = _paths(tmp_path)
    result = {(r["segment"], r["month"]): r["amount"] for r in _records(monthly_revenue_by_segment(t, c))}
    assert result[("premium", "2024-02")] == 350.0


def test_monthly_revenue_standard(tmp_path):
    t, c, _ = _paths(tmp_path)
    result = {(r["segment"], r["month"]): r["amount"] for r in _records(monthly_revenue_by_segment(t, c))}
    assert result[("standard", "2024-01")] == 120.0
    assert result[("standard", "2024-02")] == 60.0


def test_rolling_revenue_trend_columns(tmp_path):
    t, _, _ = _paths(tmp_path)
    assert _columns(rolling_revenue_trend(t)) == ["transaction_date", "amount", "rolling_avg"]


def test_rolling_revenue_trend_first_row(tmp_path):
    t, _, _ = _paths(tmp_path)
    result = _records(rolling_revenue_trend(t))
    first = next(r for r in result if r["transaction_date"] == "2024-01-10")
    assert first["rolling_avg"] == 50.0


def test_rolling_revenue_trend_window3(tmp_path):
    t, _, _ = _paths(tmp_path)
    result = {r["transaction_date"]: r["rolling_avg"] for r in _records(rolling_revenue_trend(t))}
    assert result["2024-01-20"] == 83.33
    assert result["2024-02-05"] == 133.33


def test_category_sales_report_columns(tmp_path):
    t, _, p = _paths(tmp_path)
    assert _columns(category_sales_report(t, p)) == [
        "category_clean", "total_sales", "avg_transaction", "transaction_count"
    ]


def test_category_sales_report_electronics(tmp_path):
    t, _, p = _paths(tmp_path)
    result = {r["category_clean"]: r for r in _records(category_sales_report(t, p))}
    assert result["electronics"]["total_sales"] == 390.0
    assert result["electronics"]["transaction_count"] == 4


def test_category_sales_report_furniture(tmp_path):
    t, _, p = _paths(tmp_path)
    result = {r["category_clean"]: r for r in _records(category_sales_report(t, p))}
    assert result["furniture"]["total_sales"] == 270.0
    assert result["furniture"]["avg_transaction"] == 90.0


def test_category_sales_sorted_descending(tmp_path):
    t, _, p = _paths(tmp_path)
    result = _records(category_sales_report(t, p))
    totals = [r["total_sales"] for r in result]
    assert totals == sorted(totals, reverse=True)


def test_segment_category_pivot_has_segment(tmp_path):
    t, c, p = _paths(tmp_path)
    assert "segment" in _columns(segment_category_pivot(t, c, p))


def test_segment_category_pivot_has_categories(tmp_path):
    t, c, p = _paths(tmp_path)
    cols = _columns(segment_category_pivot(t, c, p))
    assert "electronics" in cols
    assert "furniture" in cols


def test_segment_category_pivot_premium_values(tmp_path):
    t, c, p = _paths(tmp_path)
    result = {r["segment"]: r for r in _records(segment_category_pivot(t, c, p))}
    assert result["premium"]["electronics"] == 330.0
    assert result["premium"]["furniture"] == 150.0


def test_segment_category_pivot_standard_values(tmp_path):
    t, c, p = _paths(tmp_path)
    result = {r["segment"]: r for r in _records(segment_category_pivot(t, c, p))}
    assert result["standard"]["electronics"] == 60.0
    assert result["standard"]["furniture"] == 120.0


def test_segment_category_pivot_sorted(tmp_path):
    t, c, p = _paths(tmp_path)
    segments = [r["segment"] for r in _records(segment_category_pivot(t, c, p))]
    assert segments == sorted(segments)
