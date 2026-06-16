from __future__ import annotations

from pathlib import Path

from sales.analytics import add_category_deviation, add_region_share, rank_within_group

ROWS = [
    "sale_id,region,product_id,category,revenue,price",
    "S1,north,P1,electronics,100,50.0",
    "S2,north,P2,tools,80,30.0",
    "S3,south,P3,electronics,120,60.0",
    "S4,south,P4,electronics,60,45.0",
    "S5,north,P5,tools,40,25.0",
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


def test_region_share_columns(tmp_path):
    assert _columns(add_region_share(_csv(tmp_path))) == [
        "sale_id",
        "region",
        "revenue",
        "share",
    ]


def test_region_share_row_count_preserved(tmp_path):
    assert len(_records(add_region_share(_csv(tmp_path)))) == 5


def test_region_share_north_total(tmp_path):
    result = {r["sale_id"]: r for r in _records(add_region_share(_csv(tmp_path)))}
    assert result["S1"]["share"] == round(100 / 220, 4)
    assert result["S2"]["share"] == round(80 / 220, 4)
    assert result["S5"]["share"] == round(40 / 220, 4)


def test_region_share_south_total(tmp_path):
    result = {r["sale_id"]: r for r in _records(add_region_share(_csv(tmp_path)))}
    assert result["S3"]["share"] == round(120 / 180, 4)
    assert result["S4"]["share"] == round(60 / 180, 4)


def test_region_share_sums_to_one_per_region(tmp_path):
    records = _records(add_region_share(_csv(tmp_path)))
    north_sum = sum(r["share"] for r in records if r["region"] == "north")
    south_sum = sum(r["share"] for r in records if r["region"] == "south")
    assert abs(north_sum - 1.0) < 0.001
    assert abs(south_sum - 1.0) < 0.001


def test_category_deviation_columns(tmp_path):
    assert _columns(add_category_deviation(_csv(tmp_path))) == [
        "product_id",
        "category",
        "price",
        "deviation",
    ]


def test_category_deviation_row_count_preserved(tmp_path):
    assert len(_records(add_category_deviation(_csv(tmp_path)))) == 5


def test_category_deviation_electronics(tmp_path):
    result = {
        r["product_id"]: r["deviation"]
        for r in _records(add_category_deviation(_csv(tmp_path)))
    }
    cat_mean = round((50.0 + 60.0 + 45.0) / 3, 10)
    assert result["P1"] == round(50.0 - cat_mean, 2)
    assert result["P3"] == round(60.0 - cat_mean, 2)
    assert result["P4"] == round(45.0 - cat_mean, 2)


def test_category_deviation_tools(tmp_path):
    result = {
        r["product_id"]: r["deviation"]
        for r in _records(add_category_deviation(_csv(tmp_path)))
    }
    assert result["P2"] == 2.5
    assert result["P5"] == -2.5


def test_rank_within_group_columns(tmp_path):
    assert _columns(rank_within_group(_csv(tmp_path))) == [
        "sale_id",
        "region",
        "revenue",
        "rank",
    ]


def test_rank_within_group_north(tmp_path):
    records = _records(rank_within_group(_csv(tmp_path)))
    north = {r["sale_id"]: r["rank"] for r in records if r["region"] == "north"}
    assert north["S1"] == 1
    assert north["S2"] == 2
    assert north["S5"] == 3


def test_rank_within_group_south(tmp_path):
    records = _records(rank_within_group(_csv(tmp_path)))
    south = {r["sale_id"]: r["rank"] for r in records if r["region"] == "south"}
    assert south["S3"] == 1
    assert south["S4"] == 2


def test_rank_within_group_row_count_preserved(tmp_path):
    assert len(_records(rank_within_group(_csv(tmp_path)))) == 5
