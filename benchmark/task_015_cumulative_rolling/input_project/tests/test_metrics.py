from __future__ import annotations

from pathlib import Path

from finance.metrics import (
    cumulative_revenue_by_product,
    daily_growth_rate,
    rolling_daily_average,
)

MULTI_ROWS = [
    "product_id,date,revenue",
    "A,2024-01-01,100",
    "A,2024-01-02,150",
    "A,2024-01-03,120",
    "B,2024-01-01,200",
    "B,2024-01-02,180",
    "B,2024-01-03,220",
]

DAILY_ROWS = [
    "date,revenue",
    "2024-01-01,100",
    "2024-01-02,200",
    "2024-01-03,150",
    "2024-01-04,300",
]


def _csv(tmp_path: Path, rows: list[str], name="data.csv") -> Path:
    p = tmp_path / name
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def _records(frame):
    if hasattr(frame, "to_dicts"):
        return frame.to_dicts()
    return frame.to_dict(orient="records")


def _columns(frame):
    return list(frame.columns)


def test_cumulative_revenue_columns(tmp_path):
    p = _csv(tmp_path, MULTI_ROWS, "multi.csv")
    assert _columns(cumulative_revenue_by_product(p)) == [
        "product_id",
        "date",
        "revenue",
        "cumulative_revenue",
    ]


def test_cumulative_revenue_product_a(tmp_path):
    p = _csv(tmp_path, MULTI_ROWS, "multi.csv")
    result = [
        r for r in _records(cumulative_revenue_by_product(p)) if r["product_id"] == "A"
    ]
    result.sort(key=lambda r: r["date"])
    assert result[0]["cumulative_revenue"] == 100
    assert result[1]["cumulative_revenue"] == 250
    assert result[2]["cumulative_revenue"] == 370


def test_cumulative_revenue_product_b(tmp_path):
    p = _csv(tmp_path, MULTI_ROWS, "multi.csv")
    result = [
        r for r in _records(cumulative_revenue_by_product(p)) if r["product_id"] == "B"
    ]
    result.sort(key=lambda r: r["date"])
    assert result[0]["cumulative_revenue"] == 200
    assert result[1]["cumulative_revenue"] == 380
    assert result[2]["cumulative_revenue"] == 600


def test_rolling_daily_average_columns(tmp_path):
    p = _csv(tmp_path, DAILY_ROWS, "daily.csv")
    assert _columns(rolling_daily_average(p)) == ["date", "revenue", "rolling_avg"]


def test_rolling_daily_average_window3(tmp_path):
    p = _csv(tmp_path, DAILY_ROWS, "daily.csv")
    result = _records(rolling_daily_average(p))
    by_date = {r["date"]: r["rolling_avg"] for r in result}
    assert by_date["2024-01-01"] == 100.0
    assert by_date["2024-01-02"] == 150.0
    assert by_date["2024-01-03"] == 150.0
    assert by_date["2024-01-04"] == 216.67


def test_rolling_daily_average_window2(tmp_path):
    p = _csv(tmp_path, DAILY_ROWS, "daily.csv")
    result = _records(rolling_daily_average(p, window=2))
    by_date = {r["date"]: r["rolling_avg"] for r in result}
    assert by_date["2024-01-01"] == 100.0
    assert by_date["2024-01-02"] == 150.0
    assert by_date["2024-01-03"] == 175.0


def test_daily_growth_rate_columns(tmp_path):
    p = _csv(tmp_path, DAILY_ROWS, "daily.csv")
    assert _columns(daily_growth_rate(p)) == ["date", "revenue", "growth_pct"]


def test_daily_growth_rate_first_row_zero(tmp_path):
    p = _csv(tmp_path, DAILY_ROWS, "daily.csv")
    result = _records(daily_growth_rate(p))
    first = next(r for r in result if r["date"] == "2024-01-01")
    assert first["growth_pct"] == 0.0


def test_daily_growth_rate_values(tmp_path):
    p = _csv(tmp_path, DAILY_ROWS, "daily.csv")
    result = {r["date"]: r["growth_pct"] for r in _records(daily_growth_rate(p))}
    assert result["2024-01-02"] == 1.0
    assert result["2024-01-03"] == -0.25
    assert result["2024-01-04"] == 1.0
