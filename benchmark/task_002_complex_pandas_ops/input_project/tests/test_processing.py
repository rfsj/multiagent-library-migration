from __future__ import annotations

from pathlib import Path

from analytics.processing import (
    customer_lifetime_value,
    invalid_sales_rows,
    latest_order_per_customer,
    load_sales,
    monthly_product_matrix,
    paid_high_value_orders,
    revenue_by_region,
)


SALES_ROWS = [
    "order_id,customer_id,product,region,status,quantity,unit_price,discount,order_date",
    "1,C001,book,north,paid,2,40.0,0.0,2025-01-05",
    "2,C002,pen,south,paid,10,5.0,0.1,2025-01-06",
    "3,C001,notebook,north,pending,3,15.0,,2025-01-07",
    "4,C003,book,east,paid,5,40.0,0.2,2025-02-01",
    "5,C004,desk,west,cancelled,1,300.0,0.0,2025-02-02",
    "6,C002,desk,south,paid,1,300.0,0.05,2025-02-03",
    "7,C005,book,north,paid,1,40.0,,not-a-date",
    "8,C003,pen,east,paid,4,5.0,0.0,2025-02-05",
    "9,C006,desk,north,paid,0,250.0,0.0,2025-03-01",
    "10,C007,book,south,unknown,1,30.0,0.0,2025-03-02",
]

CUSTOMER_ROWS = [
    "customer_id,name,signup_region",
    "C001,Ana,north",
    "C002,Beto,south",
    "C003,Clara,east",
    "C004,Dan,west",
    "C999,Zoe,north",
]


def _write_csv(tmp_path: Path, name: str, rows: list[str]) -> Path:
    path = tmp_path / name
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _records(frame):
    if hasattr(frame, "to_dicts"):
        return frame.to_dicts()
    return frame.to_dict(orient="records")


def _rounded_records(frame):
    records = _records(frame)
    rounded = []
    for record in records:
        rounded.append({
            key: round(value, 2) if isinstance(value, float) else value
            for key, value in record.items()
        })
    return rounded


def _columns(frame):
    return list(frame.columns)


def test_load_sales_adds_revenue_fields_and_sorts(tmp_path: Path):
    sales_path = _write_csv(tmp_path, "sales.csv", SALES_ROWS)

    result = load_sales(sales_path)

    assert _columns(result) == [
        "order_id",
        "customer_id",
        "product",
        "region",
        "status",
        "quantity",
        "unit_price",
        "discount",
        "order_date",
        "revenue",
        "net_revenue",
    ]
    records = _rounded_records(result)
    assert records[0]["order_id"] == 1
    assert records[0]["revenue"] == 80.0
    assert records[0]["net_revenue"] == 80.0
    assert records[-1]["order_id"] == 7
    assert records[-1]["discount"] == 0.0


def test_paid_high_value_orders_filters_and_sorts(tmp_path: Path):
    sales_path = _write_csv(tmp_path, "sales.csv", SALES_ROWS)

    result = paid_high_value_orders(sales_path, minimum_total=100.0)

    assert _columns(result) == ["order_id", "customer_id", "region", "net_revenue"]
    assert _rounded_records(result) == [
        {"order_id": 6, "customer_id": "C002", "region": "south", "net_revenue": 285.0},
        {"order_id": 4, "customer_id": "C003", "region": "east", "net_revenue": 160.0},
    ]


def test_revenue_by_region_groups_paid_orders(tmp_path: Path):
    sales_path = _write_csv(tmp_path, "sales.csv", SALES_ROWS)

    result = revenue_by_region(sales_path)

    assert _columns(result) == ["region", "total_revenue", "orders", "average_order_value"]
    assert _rounded_records(result) == [
        {"region": "south", "total_revenue": 330.0, "orders": 2, "average_order_value": 165.0},
        {"region": "east", "total_revenue": 180.0, "orders": 2, "average_order_value": 90.0},
        {"region": "north", "total_revenue": 120.0, "orders": 3, "average_order_value": 40.0},
    ]


def test_customer_lifetime_value_left_joins_customers(tmp_path: Path):
    sales_path = _write_csv(tmp_path, "sales.csv", SALES_ROWS)
    customers_path = _write_csv(tmp_path, "customers.csv", CUSTOMER_ROWS)

    result = customer_lifetime_value(sales_path, customers_path)

    assert _columns(result) == [
        "customer_id",
        "name",
        "signup_region",
        "total_spend",
        "paid_orders",
        "segment",
    ]
    assert _rounded_records(result) == [
        {
            "customer_id": "C002",
            "name": "Beto",
            "signup_region": "south",
            "total_spend": 330.0,
            "paid_orders": 2,
            "segment": "vip",
        },
        {
            "customer_id": "C003",
            "name": "Clara",
            "signup_region": "east",
            "total_spend": 180.0,
            "paid_orders": 2,
            "segment": "standard",
        },
        {
            "customer_id": "C001",
            "name": "Ana",
            "signup_region": "north",
            "total_spend": 80.0,
            "paid_orders": 1,
            "segment": "standard",
        },
        {
            "customer_id": "C004",
            "name": "Dan",
            "signup_region": "west",
            "total_spend": 0.0,
            "paid_orders": 0,
            "segment": "standard",
        },
        {
            "customer_id": "C999",
            "name": "Zoe",
            "signup_region": "north",
            "total_spend": 0.0,
            "paid_orders": 0,
            "segment": "standard",
        },
    ]


def test_monthly_product_matrix_pivots_paid_revenue(tmp_path: Path):
    sales_path = _write_csv(tmp_path, "sales.csv", SALES_ROWS)

    result = monthly_product_matrix(sales_path)

    assert _columns(result) == ["month", "book", "desk", "pen"]
    assert _rounded_records(result) == [
        {"month": "2025-01", "book": 80.0, "desk": 0.0, "pen": 45.0},
        {"month": "2025-02", "book": 160.0, "desk": 285.0, "pen": 20.0},
        {"month": "2025-03", "book": 0.0, "desk": 0.0, "pen": 0.0},
    ]


def test_latest_order_per_customer_keeps_latest_record(tmp_path: Path):
    sales_path = _write_csv(tmp_path, "sales.csv", SALES_ROWS)

    result = latest_order_per_customer(sales_path)
    records = _rounded_records(result)

    assert _columns(result) == ["customer_id", "order_id", "status", "order_date", "net_revenue"]
    assert [(record["customer_id"], record["order_id"]) for record in records] == [
        ("C001", 3),
        ("C002", 6),
        ("C003", 8),
        ("C004", 5),
        ("C005", 7),
        ("C006", 9),
        ("C007", 10),
    ]


def test_invalid_sales_rows_detects_bad_values(tmp_path: Path):
    sales_path = _write_csv(tmp_path, "sales.csv", SALES_ROWS)

    result = invalid_sales_rows(sales_path)

    assert _columns(result) == ["order_id", "customer_id", "status", "quantity", "unit_price"]
    assert _records(result) == [
        {"order_id": 9, "customer_id": "C006", "status": "paid", "quantity": 0, "unit_price": 250.0},
        {"order_id": 10, "customer_id": "C007", "status": "unknown", "quantity": 1, "unit_price": 30.0},
        {"order_id": 7, "customer_id": "C005", "status": "paid", "quantity": 1, "unit_price": 40.0},
    ]
