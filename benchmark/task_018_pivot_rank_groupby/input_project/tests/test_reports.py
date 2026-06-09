from __future__ import annotations

from pathlib import Path

from warehouse.reports import region_category_stats, sales_pivot, top_product_per_region

ROWS = [
    "region,category,product,amount",
    "north,electronics,TV,100",
    "north,electronics,Phone,50",
    "north,furniture,Chair,30",
    "south,furniture,Table,80",
    "south,electronics,Laptop,200",
    "south,furniture,Desk,40",
]


def _csv(tmp_path: Path, rows=None) -> Path:
    p = tmp_path / "sales.csv"
    p.write_text("\n".join(rows or ROWS) + "\n", encoding="utf-8")
    return p


def _records(frame):
    if hasattr(frame, "to_dicts"):
        return frame.to_dicts()
    return frame.to_dict(orient="records")


def _columns(frame):
    return list(frame.columns)


def test_sales_pivot_has_region_column(tmp_path):
    result = sales_pivot(_csv(tmp_path))
    assert "region" in _columns(result)


def test_sales_pivot_has_category_columns(tmp_path):
    cols = _columns(sales_pivot(_csv(tmp_path)))
    assert "electronics" in cols
    assert "furniture" in cols


def test_sales_pivot_north_values(tmp_path):
    result = {r["region"]: r for r in _records(sales_pivot(_csv(tmp_path)))}
    assert result["north"]["electronics"] == 150
    assert result["north"]["furniture"] == 30


def test_sales_pivot_south_values(tmp_path):
    result = {r["region"]: r for r in _records(sales_pivot(_csv(tmp_path)))}
    assert result["south"]["electronics"] == 200
    assert result["south"]["furniture"] == 120


def test_sales_pivot_sorted_by_region(tmp_path):
    regions = [r["region"] for r in _records(sales_pivot(_csv(tmp_path)))]
    assert regions == sorted(regions)


def test_top_product_per_region_columns(tmp_path):
    assert _columns(top_product_per_region(_csv(tmp_path))) == ["region", "product", "amount"]


def test_top_product_per_region_north(tmp_path):
    result = {r["region"]: r for r in _records(top_product_per_region(_csv(tmp_path)))}
    assert result["north"]["product"] == "TV"
    assert result["north"]["amount"] == 100


def test_top_product_per_region_south(tmp_path):
    result = {r["region"]: r for r in _records(top_product_per_region(_csv(tmp_path)))}
    assert result["south"]["product"] == "Laptop"
    assert result["south"]["amount"] == 200


def test_top_product_per_region_sorted(tmp_path):
    regions = [r["region"] for r in _records(top_product_per_region(_csv(tmp_path)))]
    assert regions == sorted(regions)


def test_region_category_stats_columns(tmp_path):
    assert _columns(region_category_stats(_csv(tmp_path))) == [
        "region", "category", "total", "avg", "count"
    ]


def test_region_category_stats_north_electronics(tmp_path):
    result = {
        (r["region"], r["category"]): r
        for r in _records(region_category_stats(_csv(tmp_path)))
    }
    row = result[("north", "electronics")]
    assert row["total"] == 150
    assert row["avg"] == 75.0
    assert row["count"] == 2


def test_region_category_stats_south_furniture(tmp_path):
    result = {
        (r["region"], r["category"]): r
        for r in _records(region_category_stats(_csv(tmp_path)))
    }
    row = result[("south", "furniture")]
    assert row["total"] == 120
    assert row["avg"] == 60.0
    assert row["count"] == 2


def test_region_category_stats_sorted(tmp_path):
    result = _records(region_category_stats(_csv(tmp_path)))
    pairs = [(r["region"], r["category"]) for r in result]
    assert pairs == sorted(pairs)
