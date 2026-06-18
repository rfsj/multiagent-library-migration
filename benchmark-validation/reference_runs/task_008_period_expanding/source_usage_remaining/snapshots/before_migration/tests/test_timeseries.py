from __future__ import annotations

from pathlib import Path

from metrics.timeseries import expanding_cumulative, monthly_totals, quarterly_summary

ROWS = [
    "date,amount",
    "2024-01-10,100",
    "2024-01-20,200",
    "2024-02-05,150",
    "2024-02-18,300",
    "2024-03-01,250",
    "2024-04-12,180",
]


def _csv(tmp_path: Path, rows=None) -> Path:
    p = tmp_path / "series.csv"
    p.write_text("\n".join(rows or ROWS) + "\n", encoding="utf-8")
    return p


def _records(frame):
    if hasattr(frame, "to_dicts"):
        return frame.to_dicts()
    return frame.to_dict(orient="records")


def _columns(frame):
    return list(frame.columns)


def test_monthly_totals_columns(tmp_path):
    assert _columns(monthly_totals(_csv(tmp_path))) == ["period", "amount"]


def test_monthly_totals_values(tmp_path):
    result = {
        r["period"]: r["amount"] for r in _records(monthly_totals(_csv(tmp_path)))
    }
    assert result["2024-01"] == 300
    assert result["2024-02"] == 450
    assert result["2024-03"] == 250
    assert result["2024-04"] == 180


def test_monthly_totals_sorted(tmp_path):
    periods = [r["period"] for r in _records(monthly_totals(_csv(tmp_path)))]
    assert periods == sorted(periods)


def test_quarterly_summary_columns(tmp_path):
    assert _columns(quarterly_summary(_csv(tmp_path))) == ["quarter", "total", "count"]


def test_quarterly_summary_q1(tmp_path):
    result = {r["quarter"]: r for r in _records(quarterly_summary(_csv(tmp_path)))}
    assert result["2024Q1"]["total"] == 1000
    assert result["2024Q1"]["count"] == 5


def test_quarterly_summary_q2(tmp_path):
    result = {r["quarter"]: r for r in _records(quarterly_summary(_csv(tmp_path)))}
    assert result["2024Q2"]["total"] == 180
    assert result["2024Q2"]["count"] == 1


def test_quarterly_summary_sorted(tmp_path):
    quarters = [r["quarter"] for r in _records(quarterly_summary(_csv(tmp_path)))]
    assert quarters == sorted(quarters)


def test_expanding_cumulative_columns(tmp_path):
    assert _columns(expanding_cumulative(_csv(tmp_path))) == [
        "date",
        "amount",
        "cum_sum",
        "cum_mean",
    ]


def test_expanding_cumulative_row_count(tmp_path):
    assert len(_records(expanding_cumulative(_csv(tmp_path)))) == 6


def test_expanding_cumulative_first_row(tmp_path):
    result = _records(expanding_cumulative(_csv(tmp_path)))
    first = next(r for r in result if r["date"] == "2024-01-10")
    assert first["cum_sum"] == 100
    assert first["cum_mean"] == 100.0


def test_expanding_cumulative_third_row(tmp_path):
    result = {r["date"]: r for r in _records(expanding_cumulative(_csv(tmp_path)))}
    assert result["2024-02-05"]["cum_sum"] == 450
    assert result["2024-02-05"]["cum_mean"] == 150.0


def test_expanding_cumulative_last_row(tmp_path):
    result = {r["date"]: r for r in _records(expanding_cumulative(_csv(tmp_path)))}
    assert result["2024-04-12"]["cum_sum"] == 1180
    assert result["2024-04-12"]["cum_mean"] == 196.67
