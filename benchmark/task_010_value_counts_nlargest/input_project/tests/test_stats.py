from __future__ import annotations

from pathlib import Path

from reviews.stats import (
    brand_unique_categories,
    category_distribution,
    lowest_rated_products,
    top_rated_products,
)

ROWS = [
    "product_id,name,brand,category,rating",
    "P1,Widget A,Acme,gadgets,4.5",
    "P2,Widget B,Bolt,gadgets,3.2",
    "P3,Gizmo X,Acme,tools,4.8",
    "P4,Gizmo Y,Delta,tools,4.1",
    "P5,Gadget Z,Bolt,gadgets,4.7",
    "P6,Thing W,Delta,appliances,3.9",
    "P7,Device Q,Acme,gadgets,2.1",
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


def test_category_distribution_columns(tmp_path):
    assert _columns(category_distribution(_csv(tmp_path))) == ["category", "count"]


def test_category_distribution_values(tmp_path):
    result = {r["category"]: r["count"] for r in _records(category_distribution(_csv(tmp_path)))}
    assert result["gadgets"] == 4
    assert result["tools"] == 2
    assert result["appliances"] == 1


def test_category_distribution_sorted(tmp_path):
    cats = [r["category"] for r in _records(category_distribution(_csv(tmp_path)))]
    assert cats == sorted(cats)


def test_top_rated_products_columns(tmp_path):
    assert _columns(top_rated_products(_csv(tmp_path))) == ["product_id", "name", "rating"]


def test_top_rated_products_n3(tmp_path):
    result = _records(top_rated_products(_csv(tmp_path), n=3))
    ratings = [r["rating"] for r in result]
    assert sorted(ratings, reverse=True) == ratings
    assert ratings[0] == 4.8
    assert ratings[1] == 4.7
    assert ratings[2] == 4.5


def test_top_rated_products_n1(tmp_path):
    result = _records(top_rated_products(_csv(tmp_path), n=1))
    assert len(result) == 1
    assert result[0]["product_id"] == "P3"


def test_lowest_rated_products_columns(tmp_path):
    assert _columns(lowest_rated_products(_csv(tmp_path))) == ["product_id", "name", "rating"]


def test_lowest_rated_products_n2(tmp_path):
    result = _records(lowest_rated_products(_csv(tmp_path), n=2))
    ratings = [r["rating"] for r in result]
    assert ratings[0] == 2.1
    assert ratings[1] == 3.2


def test_brand_unique_categories_columns(tmp_path):
    assert _columns(brand_unique_categories(_csv(tmp_path))) == ["brand", "unique_categories"]


def test_brand_unique_categories_values(tmp_path):
    result = {r["brand"]: r["unique_categories"] for r in _records(brand_unique_categories(_csv(tmp_path)))}
    assert result["Acme"] == 2
    assert result["Bolt"] == 1
    assert result["Delta"] == 2


def test_brand_unique_categories_sorted(tmp_path):
    brands = [r["brand"] for r in _records(brand_unique_categories(_csv(tmp_path)))]
    assert brands == sorted(brands)
