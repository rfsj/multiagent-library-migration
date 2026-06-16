from __future__ import annotations

from pathlib import Path

from events.analysis import events_per_date, extract_time_features, filter_by_hour_range

ROWS = [
    "event_id,timestamp,event_type",
    "1,2024-03-15 09:30:00,login",
    "2,2024-03-15 19:45:00,purchase",
    "3,2024-03-16 08:00:00,view",
    "4,2024-03-16 20:00:00,logout",
    "5,2024-03-17 14:30:00,login",
    "6,2024-03-17 22:15:00,purchase",
]


def _csv(tmp_path: Path, rows=None) -> Path:
    p = tmp_path / "events.csv"
    p.write_text("\n".join(rows or ROWS) + "\n", encoding="utf-8")
    return p


def _records(frame):
    if hasattr(frame, "to_dicts"):
        return frame.to_dicts()
    return frame.to_dict(orient="records")


def _columns(frame):
    return list(frame.columns)


def test_extract_time_features_columns(tmp_path):
    assert _columns(extract_time_features(_csv(tmp_path))) == [
        "event_id",
        "year",
        "month",
        "day",
        "hour",
    ]


def test_extract_time_features_values(tmp_path):
    result = {r["event_id"]: r for r in _records(extract_time_features(_csv(tmp_path)))}
    assert result[1]["year"] == 2024
    assert result[1]["month"] == 3
    assert result[1]["day"] == 15
    assert result[1]["hour"] == 9


def test_extract_time_features_event2(tmp_path):
    result = {r["event_id"]: r for r in _records(extract_time_features(_csv(tmp_path)))}
    assert result[2]["hour"] == 19
    assert result[6]["hour"] == 22


def test_extract_time_features_sorted(tmp_path):
    ids = [r["event_id"] for r in _records(extract_time_features(_csv(tmp_path)))]
    assert ids == sorted(ids)


def test_events_per_date_columns(tmp_path):
    assert _columns(events_per_date(_csv(tmp_path))) == ["date", "count"]


def test_events_per_date_counts(tmp_path):
    result = {r["date"]: r["count"] for r in _records(events_per_date(_csv(tmp_path)))}
    assert result["2024-03-15"] == 2
    assert result["2024-03-16"] == 2
    assert result["2024-03-17"] == 2


def test_events_per_date_sorted(tmp_path):
    dates = [r["date"] for r in _records(events_per_date(_csv(tmp_path)))]
    assert dates == sorted(dates)


def test_filter_by_hour_range_columns(tmp_path):
    assert _columns(filter_by_hour_range(_csv(tmp_path), 8, 18)) == [
        "event_id",
        "timestamp",
        "event_type",
    ]


def test_filter_by_hour_range_includes_boundary(tmp_path):
    result = {
        r["event_id"] for r in _records(filter_by_hour_range(_csv(tmp_path), 8, 18))
    }
    assert 1 in result
    assert 3 in result
    assert 5 in result


def test_filter_by_hour_range_excludes_outside(tmp_path):
    result = {
        r["event_id"] for r in _records(filter_by_hour_range(_csv(tmp_path), 8, 18))
    }
    assert 2 not in result
    assert 4 not in result
    assert 6 not in result


def test_filter_by_hour_range_empty(tmp_path):
    result = _records(filter_by_hour_range(_csv(tmp_path), 0, 6))
    assert result == []
