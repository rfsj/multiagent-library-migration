from pathlib import Path

from orders.processing import get_paid_orders


def _records(frame):
    if hasattr(frame, "to_dicts"):
        return frame.to_dicts()
    return frame.to_dict(orient="records")


def test_get_paid_orders_filters_selects_and_sorts(tmp_path: Path):
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text(
        "order_id,customer_id,status,total\n"
        "1,C003,pending,30.0\n"
        "2,C001,paid,20.5\n"
        "3,C002,paid,10.0\n",
        encoding="utf-8",
    )

    result = get_paid_orders(csv_path)

    assert _records(result) == [
        {"customer_id": "C002", "total": 10.0},
        {"customer_id": "C001", "total": 20.5},
    ]
