from __future__ import annotations

from pathlib import Path

from logistics.fulfillment import fulfillment_rate, pending_orders
from logistics.orders import order_totals, orders_by_customer_category
from logistics.products import load_active_products, products_by_category

PRODUCT_ROWS = [
    "product_id,name,category,unit_price,active",
    "PR1,Widget A,electronics,25.0,True",
    "PR2,Widget B,electronics,40.0,True",
    "PR3,Gadget X,tools,15.0,False",
    "PR4,Gadget Y,tools,20.0,True",
    "PR5,Device Z,electronics,60.0,True",
]

ORDER_ROWS = [
    "order_id,customer_id,product_id,quantity",
    "O1,CU1,PR1,2",
    "O1,CU1,PR2,1",
    "O2,CU2,PR4,3",
    "O3,CU1,PR5,1",
    "O4,CU3,PR1,4",
]

SHIPMENT_ROWS = [
    "shipment_id,order_id,status",
    "S1,O1,shipped",
    "S2,O2,shipped",
    "S3,O3,pending",
    "S4,O4,pending",
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
    p = _csv(tmp_path, PRODUCT_ROWS, "products.csv")
    o = _csv(tmp_path, ORDER_ROWS, "orders.csv")
    s = _csv(tmp_path, SHIPMENT_ROWS, "shipments.csv")
    return p, o, s


def test_load_active_products_columns(tmp_path):
    p, _, _ = _paths(tmp_path)
    assert _columns(load_active_products(p)) == ["product_id", "name", "category", "unit_price"]


def test_load_active_products_excludes_inactive(tmp_path):
    p, _, _ = _paths(tmp_path)
    ids = {r["product_id"] for r in _records(load_active_products(p))}
    assert "PR3" not in ids
    assert ids == {"PR1", "PR2", "PR4", "PR5"}


def test_products_by_category_columns(tmp_path):
    p, _, _ = _paths(tmp_path)
    assert _columns(products_by_category(p)) == ["category", "product_count", "avg_price"]


def test_products_by_category_electronics(tmp_path):
    p, _, _ = _paths(tmp_path)
    result = {r["category"]: r for r in _records(products_by_category(p))}
    assert result["electronics"]["product_count"] == 3
    assert result["electronics"]["avg_price"] == 41.67


def test_products_by_category_tools_excludes_inactive(tmp_path):
    p, _, _ = _paths(tmp_path)
    result = {r["category"]: r for r in _records(products_by_category(p))}
    assert result["tools"]["product_count"] == 1
    assert result["tools"]["avg_price"] == 20.0


def test_order_totals_columns(tmp_path):
    p, o, _ = _paths(tmp_path)
    assert _columns(order_totals(p, o)) == ["order_id", "total_amount"]


def test_order_totals_values(tmp_path):
    p, o, _ = _paths(tmp_path)
    result = {r["order_id"]: r["total_amount"] for r in _records(order_totals(p, o))}
    assert result["O1"] == 90.0
    assert result["O2"] == 60.0
    assert result["O3"] == 60.0
    assert result["O4"] == 100.0


def test_orders_by_customer_category_columns(tmp_path):
    p, o, _ = _paths(tmp_path)
    assert _columns(orders_by_customer_category(p, o)) == [
        "customer_id", "category", "total_spent"
    ]


def test_orders_by_customer_category_cu1(tmp_path):
    p, o, _ = _paths(tmp_path)
    result = {
        (r["customer_id"], r["category"]): r["total_spent"]
        for r in _records(orders_by_customer_category(p, o))
    }
    assert result[("CU1", "electronics")] == 150.0
    assert result[("CU2", "tools")] == 60.0
    assert result[("CU3", "electronics")] == 100.0


def test_fulfillment_rate_value(tmp_path):
    _, o, s = _paths(tmp_path)
    rate = fulfillment_rate(o, s)
    assert rate == 0.5


def test_pending_orders_columns(tmp_path):
    _, o, s = _paths(tmp_path)
    assert _columns(pending_orders(o, s)) == ["order_id", "customer_id"]


def test_pending_orders_values(tmp_path):
    _, o, s = _paths(tmp_path)
    ids = {r["order_id"] for r in _records(pending_orders(o, s))}
    assert ids == {"O3", "O4"}


def test_pending_orders_excludes_shipped(tmp_path):
    _, o, s = _paths(tmp_path)
    ids = {r["order_id"] for r in _records(pending_orders(o, s))}
    assert "O1" not in ids
    assert "O2" not in ids
