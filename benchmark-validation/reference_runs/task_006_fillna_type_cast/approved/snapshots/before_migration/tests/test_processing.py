from __future__ import annotations

from pathlib import Path

import pytest

from inventory.processing import available_items, expensive_items, out_of_stock_items

ROWS = [
    "sku,name,stock,price",
    "A01,Widget,10,9.99",
    "A02,Gadget,,49.99",
    "A03,Doohickey,0,5.00",
    "A04,Thingamajig,,",
    "A05,Gizmo,3,99.99",
    "A06,Contraption,1,",
]


def _csv(tmp_path: Path, rows=None) -> Path:
    p = tmp_path / "items.csv"
    p.write_text("\n".join(rows or ROWS) + "\n", encoding="utf-8")
    return p


def _records(frame):
    if hasattr(frame, "to_dicts"):
        return frame.to_dicts()
    return frame.to_dict(orient="records")


def _cols(frame):
    return list(frame.columns)


def test_available_items_excludes_zero_stock(tmp_path):
    result = available_items(_csv(tmp_path))
    skus = [r["sku"] for r in _records(result)]
    assert "A03" not in skus
    assert "A04" not in skus


def test_available_items_fills_nulls_and_casts(tmp_path):
    result = available_items(_csv(tmp_path))
    records = _records(result)
    # A02 had null stock → should be filled with 0 → excluded
    assert all(r["sku"] != "A02" for r in records)
    # A06 had null price → should be filled with 0.0
    a06 = next((r for r in records if r["sku"] == "A06"), None)
    assert a06 is not None
    assert a06["price"] == 0.0
    # stock column must be integer
    assert all(isinstance(r["stock"], int) for r in records)


def test_available_items_sorted_by_price_then_sku(tmp_path):
    result = available_items(_csv(tmp_path))
    records = _records(result)
    prices = [r["price"] for r in records]
    assert prices == sorted(prices)


def test_available_items_columns(tmp_path):
    assert _cols(available_items(_csv(tmp_path))) == ["sku", "name", "stock", "price"]


def test_out_of_stock_returns_zero_stock_items(tmp_path):
    result = out_of_stock_items(_csv(tmp_path))
    records = _records(result)
    skus = {r["sku"] for r in records}
    assert skus == {"A02", "A03", "A04"}


def test_out_of_stock_sorted_by_sku(tmp_path):
    result = out_of_stock_items(_csv(tmp_path))
    skus = [r["sku"] for r in _records(result)]
    assert skus == sorted(skus)


def test_out_of_stock_columns(tmp_path):
    assert _cols(out_of_stock_items(_csv(tmp_path))) == ["sku", "name"]


def test_expensive_items_default_threshold(tmp_path):
    result = expensive_items(_csv(tmp_path))
    records = _records(result)
    skus = {r["sku"] for r in records}
    assert skus == {"A05"}


def test_expensive_items_custom_threshold(tmp_path):
    result = expensive_items(_csv(tmp_path), min_price=10.0)
    records = _records(result)
    skus = {r["sku"] for r in records}
    assert skus == {"A02", "A05"}


def test_expensive_items_sorted_descending(tmp_path):
    result = expensive_items(_csv(tmp_path), min_price=10.0)
    prices = [r["price"] for r in _records(result)]
    assert prices == sorted(prices, reverse=True)


def test_expensive_items_null_price_excluded(tmp_path):
    result = expensive_items(_csv(tmp_path), min_price=0.01)
    records = _records(result)
    skus = {r["sku"] for r in records}
    assert "A04" not in skus


def test_expensive_items_columns(tmp_path):
    assert _cols(expensive_items(_csv(tmp_path))) == ["sku", "name", "price"]
