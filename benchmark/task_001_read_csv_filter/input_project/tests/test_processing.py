from __future__ import annotations

from pathlib import Path

import pytest

from orders.processing import (
    get_cancelled_orders,
    get_high_priority_orders,
    get_paid_orders,
    get_paid_orders_for_north_region,
    get_pending_orders,
)


ORDER_ROWS = [
    "order_id,customer_id,status,total,region,priority",
    "7,C007,paid,99.9,north,low",
    "2,C002,paid,20.5,north,high",
    "5,C005,cancelled,45.0,south,high",
    "3,C003,pending,30.0,south,low",
    "4,C004,paid,10.0,south,medium",
    "1,C001,cancelled,12.5,north,low",
    "6,C006,pending,10.0,north,medium",
    "8,C008,paid,42.0,north,low",
    "9,C009,cancelled,45.0,east,medium",
    "10,C010,paid,42.0,west,high",
]


def _records(frame):
    if hasattr(frame, "to_dicts"):
        return frame.to_dicts()
    return frame.to_dict(orient="records")


def _columns(frame):
    return list(frame.columns)


def _write_orders_csv(tmp_path: Path, rows: list[str] | None = None) -> Path:
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("\n".join(rows or ORDER_ROWS) + "\n", encoding="utf-8")
    return csv_path


@pytest.mark.parametrize(
    ("reader", "expected_columns", "expected_records"),
    [
        (
            get_paid_orders,
            ["customer_id", "total"],
            [
                {"customer_id": "C004", "total": 10.0},
                {"customer_id": "C002", "total": 20.5},
                {"customer_id": "C008", "total": 42.0},
                {"customer_id": "C010", "total": 42.0},
                {"customer_id": "C007", "total": 99.9},
            ],
        ),
        (
            get_pending_orders,
            ["order_id", "customer_id"],
            [
                {"order_id": 3, "customer_id": "C003"},
                {"order_id": 6, "customer_id": "C006"},
            ],
        ),
        (
            get_cancelled_orders,
            ["order_id", "total"],
            [
                {"order_id": 1, "total": 12.5},
                {"order_id": 5, "total": 45.0},
                {"order_id": 9, "total": 45.0},
            ],
        ),
        (
            get_paid_orders_for_north_region,
            ["order_id", "customer_id", "total"],
            [
                {"order_id": 2, "customer_id": "C002", "total": 20.5},
                {"order_id": 7, "customer_id": "C007", "total": 99.9},
                {"order_id": 8, "customer_id": "C008", "total": 42.0},
            ],
        ),
        (
            get_high_priority_orders,
            ["order_id", "status", "region"],
            [
                {"order_id": 5, "status": "cancelled", "region": "south"},
                {"order_id": 2, "status": "paid", "region": "north"},
                {"order_id": 10, "status": "paid", "region": "west"},
            ],
        ),
    ],
)
def test_order_queries_filter_project_and_sort(
    tmp_path: Path,
    reader,
    expected_columns,
    expected_records,
):
    csv_path = _write_orders_csv(tmp_path)

    result = reader(csv_path)

    assert _columns(result) == expected_columns
    assert _records(result) == expected_records


@pytest.mark.parametrize(
    ("reader", "expected_columns"),
    [
        (get_paid_orders, ["customer_id", "total"]),
        (get_pending_orders, ["order_id", "customer_id"]),
        (get_cancelled_orders, ["order_id", "total"]),
        (get_paid_orders_for_north_region, ["order_id", "customer_id", "total"]),
        (get_high_priority_orders, ["order_id", "status", "region"]),
    ],
)
def test_order_queries_preserve_empty_result_schema(
    tmp_path: Path, reader, expected_columns
):
    csv_path = _write_orders_csv(
        tmp_path,
        [
            "order_id,customer_id,status,total,region,priority",
            "1,C001,refunded,11.0,south,low",
            "2,C002,review,12.0,east,medium",
        ],
    )

    result = reader(csv_path)

    assert _columns(result) == expected_columns
    assert _records(result) == []


def test_queries_read_from_the_given_file_each_time(tmp_path: Path):
    first_csv = tmp_path / "first.csv"
    first_csv.write_text(
        "\n".join(
            [
                "order_id,customer_id,status,total,region,priority",
                "1,C001,paid,90.0,north,low",
                "2,C002,paid,10.0,south,high",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    second_csv = tmp_path / "second.csv"
    second_csv.write_text(
        "\n".join(
            [
                "order_id,customer_id,status,total,region,priority",
                "3,C003,paid,8.0,north,high",
                "4,C004,pending,7.0,south,low",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert _records(get_paid_orders(first_csv)) == [
        {"customer_id": "C002", "total": 10.0},
        {"customer_id": "C001", "total": 90.0},
    ]
    assert _records(get_paid_orders(second_csv)) == [
        {"customer_id": "C003", "total": 8.0},
    ]
