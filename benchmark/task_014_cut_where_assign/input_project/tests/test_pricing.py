from __future__ import annotations

from pathlib import Path

from products.pricing import apply_discount, enrich_with_margin, price_tier

ROWS = [
    "product_id,name,price,cost,stock",
    "P1,Item A,15.0,8.0,50",
    "P2,Item B,45.0,20.0,120",
    "P3,Item C,90.0,50.0,80",
    "P4,Item D,80.0,40.0,200",
    "P5,Item E,10.0,9.0,30",
]


def _csv(tmp_path: Path, rows=None) -> Path:
    p = tmp_path / "products.csv"
    p.write_text("\n".join(rows or ROWS) + "\n", encoding="utf-8")
    return p


def _records(frame):
    if hasattr(frame, "to_dicts"):
        return frame.to_dicts()
    return frame.to_dict(orient="records")


def _columns(frame):
    return list(frame.columns)


def test_price_tier_columns(tmp_path):
    assert _columns(price_tier(_csv(tmp_path))) == [
        "product_id",
        "name",
        "price",
        "tier",
    ]


def test_price_tier_budget(tmp_path):
    result = {r["product_id"]: r["tier"] for r in _records(price_tier(_csv(tmp_path)))}
    assert result["P1"] == "budget"
    assert result["P5"] == "budget"


def test_price_tier_mid(tmp_path):
    result = {r["product_id"]: r["tier"] for r in _records(price_tier(_csv(tmp_path)))}
    assert result["P2"] == "mid"


def test_price_tier_premium(tmp_path):
    result = {r["product_id"]: r["tier"] for r in _records(price_tier(_csv(tmp_path)))}
    assert result["P3"] == "premium"
    assert result["P4"] == "premium"


def test_price_tier_sorted(tmp_path):
    ids = [r["product_id"] for r in _records(price_tier(_csv(tmp_path)))]
    assert ids == sorted(ids)


def test_apply_discount_columns(tmp_path):
    assert _columns(apply_discount(_csv(tmp_path))) == [
        "product_id",
        "price",
        "stock",
        "discounted_price",
    ]


def test_apply_discount_stock_over_100(tmp_path):
    result = {r["product_id"]: r for r in _records(apply_discount(_csv(tmp_path)))}
    assert result["P2"]["discounted_price"] == 40.5
    assert result["P4"]["discounted_price"] == 72.0


def test_apply_discount_stock_under_100(tmp_path):
    result = {r["product_id"]: r for r in _records(apply_discount(_csv(tmp_path)))}
    assert result["P1"]["discounted_price"] == 15.0
    assert result["P3"]["discounted_price"] == 90.0
    assert result["P5"]["discounted_price"] == 10.0


def test_enrich_with_margin_columns(tmp_path):
    assert _columns(enrich_with_margin(_csv(tmp_path))) == [
        "product_id",
        "price",
        "cost",
        "margin",
        "high_margin",
    ]


def test_enrich_with_margin_values(tmp_path):
    result = {r["product_id"]: r for r in _records(enrich_with_margin(_csv(tmp_path)))}
    assert result["P1"]["margin"] == 46.7
    assert result["P1"]["high_margin"] is True
    assert result["P5"]["margin"] == 10.0
    assert result["P5"]["high_margin"] is False


def test_enrich_with_margin_sorted(tmp_path):
    ids = [r["product_id"] for r in _records(enrich_with_margin(_csv(tmp_path)))]
    assert ids == sorted(ids)
