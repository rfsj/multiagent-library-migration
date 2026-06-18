from __future__ import annotations

from pathlib import Path

from retention.features import (
    first_touch_revenue,
    user_activity_summary,
    weekly_cohort_retention,
)
from retention.loaders import load_events, load_users, valid_events


EVENT_ROWS = [
    "event_id,user_id,session_id,event_name,event_time,channel,revenue",
    "1,U001,S1, signup ,2025-01-01 08:00,email,",
    "2,U001,S1,page_view,2025-01-01 08:05,email,",
    "3,U001,S2,purchase,2025-01-03 09:00,Email,120.5",
    "4,U001,S3,page_view,2025-01-08 10:00,search,",
    "5,U002,S4,signup,2025-01-02 11:00,ads,",
    "6,U002,S5,page_view,2025-01-05 12:00,ads,",
    "7,U003,S6,signup,2025-02-01 10:00,,",
    "8,U003,S7,purchase,2025-02-01 10:30,Referral,80.0",
    "9,U003,S8,purchase,2025-02-09 09:00,referral,40.0",
    "10,U004,S9,signup,not-a-date,email,",
    "11,U004,S10,cancel,2025-02-05 15:00,email,",
    "12,U005,S11,unknown,2025-02-06 16:00,ads,",
]

USER_ROWS = [
    "user_id,signup_date,country,plan",
    "U001,2025-01-01,br,Pro",
    "U002,2025-01-02,us,",
    "U003,2025-02-01,,Team",
    "U004,2025-02-04,ca,Free",
    "U999,2025-02-10,mx,Pro",
]


def _write_csv(tmp_path: Path, name: str, rows: list[str]) -> Path:
    path = tmp_path / name
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _records(frame):
    if hasattr(frame, "to_dicts"):
        return frame.to_dicts()
    return frame.to_dict(orient="records")


def _rounded_records(frame):
    rounded = []
    for record in _records(frame):
        rounded.append(
            {
                key: round(value, 2) if isinstance(value, float) else value
                for key, value in record.items()
            }
        )
    return rounded


def _columns(frame):
    return list(frame.columns)


def test_loaders_normalize_event_and_user_fields(tmp_path: Path):
    events_path = _write_csv(tmp_path, "events.csv", EVENT_ROWS)
    users_path = _write_csv(tmp_path, "users.csv", USER_ROWS)

    events = load_events(events_path)
    users = load_users(users_path)
    valid = valid_events(events_path)

    assert _columns(events) == [
        "event_id",
        "user_id",
        "session_id",
        "event_name",
        "event_time",
        "channel",
        "revenue",
        "is_purchase",
    ]
    assert _records(events)[0]["event_name"] == "signup"
    assert _records(events)[6]["channel"] == "unknown"
    assert _records(users)[1]["plan"] == "free"
    assert _records(users)[2]["country"] == "UNKNOWN"
    assert [record["event_id"] for record in _records(valid)] == [
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        11,
    ]


def test_user_activity_summary_merges_users_with_activity(tmp_path: Path):
    events_path = _write_csv(tmp_path, "events.csv", EVENT_ROWS)
    users_path = _write_csv(tmp_path, "users.csv", USER_ROWS)

    result = user_activity_summary(events_path, users_path)

    assert _columns(result) == [
        "user_id",
        "country",
        "plan",
        "sessions",
        "purchases",
        "total_revenue",
        "days_active",
        "status",
    ]
    assert _rounded_records(result) == [
        {
            "user_id": "U001",
            "country": "BR",
            "plan": "pro",
            "sessions": 3,
            "purchases": 1,
            "total_revenue": 120.5,
            "days_active": 7,
            "status": "buyer",
        },
        {
            "user_id": "U003",
            "country": "UNKNOWN",
            "plan": "team",
            "sessions": 3,
            "purchases": 2,
            "total_revenue": 120.0,
            "days_active": 8,
            "status": "buyer",
        },
        {
            "user_id": "U002",
            "country": "US",
            "plan": "free",
            "sessions": 2,
            "purchases": 0,
            "total_revenue": 0.0,
            "days_active": 3,
            "status": "prospect",
        },
        {
            "user_id": "U004",
            "country": "CA",
            "plan": "free",
            "sessions": 1,
            "purchases": 0,
            "total_revenue": 0.0,
            "days_active": 1,
            "status": "prospect",
        },
        {
            "user_id": "U999",
            "country": "MX",
            "plan": "pro",
            "sessions": 0,
            "purchases": 0,
            "total_revenue": 0.0,
            "days_active": 0,
            "status": "prospect",
        },
    ]


def test_first_touch_revenue_keeps_channel_and_purchase_totals(tmp_path: Path):
    events_path = _write_csv(tmp_path, "events.csv", EVENT_ROWS)

    result = first_touch_revenue(events_path)

    assert _columns(result) == ["user_id", "channel", "total_revenue", "purchases"]
    assert _rounded_records(result) == [
        {"user_id": "U001", "channel": "email", "total_revenue": 120.5, "purchases": 1},
        {
            "user_id": "U003",
            "channel": "unknown",
            "total_revenue": 120.0,
            "purchases": 2,
        },
        {"user_id": "U002", "channel": "ads", "total_revenue": 0.0, "purchases": 0},
        {"user_id": "U004", "channel": "email", "total_revenue": 0.0, "purchases": 0},
    ]


def test_weekly_cohort_retention_pivots_active_days(tmp_path: Path):
    events_path = _write_csv(tmp_path, "events.csv", EVENT_ROWS)
    users_path = _write_csv(tmp_path, "users.csv", USER_ROWS)

    result = weekly_cohort_retention(events_path, users_path)

    assert _columns(result) == ["cohort", 0, 1, 2, 3, 7]
    assert _records(result) == [
        {"cohort": "2025-01", 0: 2, 1: 0, 2: 1, 3: 1, 7: 1},
        {"cohort": "2025-02", 0: 1, 1: 1, 2: 0, 3: 0, 7: 0},
    ]
