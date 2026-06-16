from __future__ import annotations

from pathlib import Path

from scoring.features import add_group_stats, add_rank_and_share
from scoring.pipeline import enrich_with_history, flag_anomalies

FEATURE_ROWS = [
    "item_id,category,value",
    "IT1,A,100",
    "IT2,A,120",
    "IT3,A,80",
    "IT4,B,50",
    "IT5,B,70",
    "IT6,B,60",
]

ITEM_ROWS = [
    "item_id,category",
    "I1,electronics",
    "I2,electronics",
    "I3,tools",
]

HISTORY_ROWS = [
    "item_id,date,sales",
    "I1,2024-01-01,100",
    "I1,2024-01-02,105",
    "I1,2024-02-01,98",
    "I1,2024-02-02,102",
    "I2,2024-01-01,95",
    "I2,2024-01-02,500",
    "I2,2024-02-01,90",
    "I3,2024-01-01,50",
    "I3,2024-01-02,55",
    "I3,2024-02-01,48",
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


def _feat(tmp_path):
    return _csv(tmp_path, FEATURE_ROWS, "features.csv")


def _items(tmp_path):
    return _csv(tmp_path, ITEM_ROWS, "items.csv")


def _history(tmp_path):
    return _csv(tmp_path, HISTORY_ROWS, "history.csv")


# --- add_group_stats ---


def test_group_stats_columns(tmp_path):
    assert _columns(add_group_stats(_feat(tmp_path))) == [
        "item_id",
        "category",
        "value",
        "group_mean",
        "z_score",
    ]


def test_group_stats_row_count(tmp_path):
    assert len(_records(add_group_stats(_feat(tmp_path)))) == 6


def test_group_stats_group_mean_a(tmp_path):
    result = {r["item_id"]: r for r in _records(add_group_stats(_feat(tmp_path)))}
    assert result["IT1"]["group_mean"] == 100.0
    assert result["IT2"]["group_mean"] == 100.0
    assert result["IT3"]["group_mean"] == 100.0


def test_group_stats_z_score_a(tmp_path):
    result = {
        r["item_id"]: r["z_score"] for r in _records(add_group_stats(_feat(tmp_path)))
    }
    assert result["IT1"] == 0.0
    assert result["IT2"] == 1.0
    assert result["IT3"] == -1.0


def test_group_stats_z_score_b(tmp_path):
    result = {
        r["item_id"]: r["z_score"] for r in _records(add_group_stats(_feat(tmp_path)))
    }
    assert result["IT4"] == -1.0
    assert result["IT5"] == 1.0
    assert result["IT6"] == 0.0


# --- add_rank_and_share ---


def test_rank_and_share_columns(tmp_path):
    assert _columns(add_rank_and_share(_feat(tmp_path))) == [
        "item_id",
        "category",
        "value",
        "rank",
        "share",
    ]


def test_rank_and_share_row_count(tmp_path):
    assert len(_records(add_rank_and_share(_feat(tmp_path)))) == 6


def test_rank_a(tmp_path):
    result = {
        r["item_id"]: r["rank"] for r in _records(add_rank_and_share(_feat(tmp_path)))
    }
    assert result["IT2"] == 1
    assert result["IT1"] == 2
    assert result["IT3"] == 3


def test_share_a(tmp_path):
    result = {
        r["item_id"]: r["share"] for r in _records(add_rank_and_share(_feat(tmp_path)))
    }
    assert result["IT2"] == round(120 / 300, 4)
    assert result["IT1"] == round(100 / 300, 4)
    assert result["IT3"] == round(80 / 300, 4)


def test_share_sums_to_one_per_category(tmp_path):
    records = _records(add_rank_and_share(_feat(tmp_path)))
    for cat in ("A", "B"):
        total = sum(r["share"] for r in records if r["category"] == cat)
        assert abs(total - 1.0) < 0.001


# --- enrich_with_history ---


def test_enrich_columns(tmp_path):
    assert _columns(enrich_with_history(_items(tmp_path), _history(tmp_path))) == [
        "item_id",
        "category",
        "period",
        "sales",
        "cum_sales",
    ]


def test_enrich_monthly_sales_i1(tmp_path):
    result = {
        (r["item_id"], r["period"]): r
        for r in _records(enrich_with_history(_items(tmp_path), _history(tmp_path)))
    }
    assert result[("I1", "2024-01")]["sales"] == 205
    assert result[("I1", "2024-02")]["sales"] == 200


def test_enrich_cum_sales_i1(tmp_path):
    result = {
        (r["item_id"], r["period"]): r
        for r in _records(enrich_with_history(_items(tmp_path), _history(tmp_path)))
    }
    assert result[("I1", "2024-01")]["cum_sales"] == 205
    assert result[("I1", "2024-02")]["cum_sales"] == 405


def test_enrich_cum_sales_i2(tmp_path):
    result = {
        (r["item_id"], r["period"]): r
        for r in _records(enrich_with_history(_items(tmp_path), _history(tmp_path)))
    }
    assert result[("I2", "2024-01")]["cum_sales"] == 595
    assert result[("I2", "2024-02")]["cum_sales"] == 685


def test_enrich_category_joined(tmp_path):
    result = {
        r["item_id"]: r["category"]
        for r in _records(enrich_with_history(_items(tmp_path), _history(tmp_path)))
    }
    assert result["I1"] == "electronics"
    assert result["I3"] == "tools"


# --- flag_anomalies ---


def test_flag_anomalies_columns(tmp_path):
    assert _columns(flag_anomalies(_items(tmp_path), _history(tmp_path))) == [
        "item_id",
        "category",
        "date",
        "sales",
        "is_anomaly",
    ]


def test_flag_anomalies_detects_spike(tmp_path):
    result = [
        r
        for r in _records(flag_anomalies(_items(tmp_path), _history(tmp_path)))
        if r["item_id"] == "I2" and r["sales"] == 500
    ]
    assert len(result) == 1
    assert result[0]["is_anomaly"] is True


def test_flag_anomalies_normal_sales_not_flagged(tmp_path):
    records = _records(flag_anomalies(_items(tmp_path), _history(tmp_path)))
    i1_anomalies = [r for r in records if r["item_id"] == "I1" and r["is_anomaly"]]
    assert i1_anomalies == []


def test_flag_anomalies_tools_not_flagged(tmp_path):
    records = _records(flag_anomalies(_items(tmp_path), _history(tmp_path)))
    i3_anomalies = [r for r in records if r["item_id"] == "I3" and r["is_anomaly"]]
    assert i3_anomalies == []


def test_flag_anomalies_row_count(tmp_path):
    assert len(_records(flag_anomalies(_items(tmp_path), _history(tmp_path)))) == 10
