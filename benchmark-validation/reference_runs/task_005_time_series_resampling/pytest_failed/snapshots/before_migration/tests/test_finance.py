from __future__ import annotations

from pathlib import Path

from finance.prices import daily_price_features, latest_close_by_symbol, load_ticks
from finance.signals import align_signals_to_prices, load_signals, signal_performance_summary


TICK_ROWS = [
    "trade_id,symbol,timestamp,price,volume",
    "1,AAA,2025-01-01 09:30,100.0,10",
    "2,AAA,2025-01-01 15:59,101.0,15",
    "3,AAA,2025-01-02 15:59,103.0,20",
    "4,AAA,2025-01-04 15:59,106.0,25",
    "5,AAA,2025-01-04 15:59,107.0,30",
    "6,BBB,2025-01-01 10:00,50.0,8",
    "7,BBB,2025-01-03 15:59,49.0,12",
    "8,BBB,2025-01-04 15:59,51.0,18",
    "9,BBB,not-a-date,52.0,5",
]

SIGNAL_ROWS = [
    "signal_id,symbol,timestamp,signal,confidence",
    "1,AAA,2024-12-31 12:00,Buy,0.50",
    "2,AAA,2025-01-02 09:00, sell ,0.80",
    "3,BBB,2025-01-01 09:00,buy,",
    "4,BBB,2025-01-04 08:00,sell,0.60",
    "5,CCC,not-a-date,buy,1.0",
]


def _write_csv(tmp_path: Path, name: str, rows: list[str]) -> Path:
    path = tmp_path / name
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _records(frame):
    if hasattr(frame, "to_dicts"):
        return frame.to_dicts()
    return frame.to_dict(orient="records")


def _simplified_records(frame):
    simplified = []
    for record in _records(frame):
        simplified.append({
            key: (
                value.strftime("%Y-%m-%d")
                if hasattr(value, "strftime")
                else round(value, 4)
                if isinstance(value, float)
                else value
            )
            for key, value in record.items()
        })
    return simplified


def _columns(frame):
    return list(frame.columns)


def test_load_ticks_parses_sorts_and_deduplicates(tmp_path: Path):
    ticks_path = _write_csv(tmp_path, "ticks.csv", TICK_ROWS)

    result = load_ticks(ticks_path)

    assert _columns(result) == ["trade_id", "symbol", "timestamp", "price", "volume"]
    assert [(record["symbol"], record["trade_id"]) for record in _records(result)] == [
        ("AAA", 1),
        ("AAA", 2),
        ("AAA", 3),
        ("AAA", 5),
        ("BBB", 6),
        ("BBB", 7),
        ("BBB", 8),
    ]


def test_daily_price_features_resamples_and_rolls_by_symbol(tmp_path: Path):
    ticks_path = _write_csv(tmp_path, "ticks.csv", TICK_ROWS)

    result = daily_price_features(ticks_path)

    assert _columns(result) == [
        "symbol",
        "timestamp",
        "close",
        "volume",
        "daily_return",
        "rolling_3d_close",
        "rolling_3d_volume",
    ]
    assert _simplified_records(result) == [
        {
            "symbol": "AAA",
            "timestamp": "2025-01-01",
            "close": 101.0,
            "volume": 25,
            "daily_return": 0.0,
            "rolling_3d_close": 101.0,
            "rolling_3d_volume": 25.0,
        },
        {
            "symbol": "AAA",
            "timestamp": "2025-01-02",
            "close": 103.0,
            "volume": 20,
            "daily_return": 0.0198,
            "rolling_3d_close": 102.0,
            "rolling_3d_volume": 45.0,
        },
        {
            "symbol": "AAA",
            "timestamp": "2025-01-03",
            "close": 103.0,
            "volume": 0,
            "daily_return": 0.0,
            "rolling_3d_close": 102.33,
            "rolling_3d_volume": 45.0,
        },
        {
            "symbol": "AAA",
            "timestamp": "2025-01-04",
            "close": 107.0,
            "volume": 30,
            "daily_return": 0.0388,
            "rolling_3d_close": 104.33,
            "rolling_3d_volume": 50.0,
        },
        {
            "symbol": "BBB",
            "timestamp": "2025-01-01",
            "close": 50.0,
            "volume": 8,
            "daily_return": 0.0,
            "rolling_3d_close": 50.0,
            "rolling_3d_volume": 8.0,
        },
        {
            "symbol": "BBB",
            "timestamp": "2025-01-02",
            "close": 50.0,
            "volume": 0,
            "daily_return": 0.0,
            "rolling_3d_close": 50.0,
            "rolling_3d_volume": 8.0,
        },
        {
            "symbol": "BBB",
            "timestamp": "2025-01-03",
            "close": 49.0,
            "volume": 12,
            "daily_return": -0.02,
            "rolling_3d_close": 49.67,
            "rolling_3d_volume": 20.0,
        },
        {
            "symbol": "BBB",
            "timestamp": "2025-01-04",
            "close": 51.0,
            "volume": 18,
            "daily_return": 0.0408,
            "rolling_3d_close": 50.0,
            "rolling_3d_volume": 30.0,
        },
    ]


def test_latest_close_by_symbol_selects_last_tick(tmp_path: Path):
    ticks_path = _write_csv(tmp_path, "ticks.csv", TICK_ROWS)

    result = latest_close_by_symbol(ticks_path)

    assert _simplified_records(result) == [
        {"symbol": "AAA", "timestamp": "2025-01-04", "price": 107.0, "volume": 30},
        {"symbol": "BBB", "timestamp": "2025-01-04", "price": 51.0, "volume": 18},
    ]


def test_load_signals_normalizes_rows(tmp_path: Path):
    signals_path = _write_csv(tmp_path, "signals.csv", SIGNAL_ROWS)

    result = load_signals(signals_path)

    assert _columns(result) == ["signal_id", "symbol", "timestamp", "signal", "confidence"]
    assert _simplified_records(result) == [
        {"signal_id": 1, "symbol": "AAA", "timestamp": "2024-12-31", "signal": "buy", "confidence": 0.5},
        {"signal_id": 2, "symbol": "AAA", "timestamp": "2025-01-02", "signal": "sell", "confidence": 0.8},
        {"signal_id": 3, "symbol": "BBB", "timestamp": "2025-01-01", "signal": "buy", "confidence": 0.0},
        {"signal_id": 4, "symbol": "BBB", "timestamp": "2025-01-04", "signal": "sell", "confidence": 0.6},
    ]


def test_align_signals_to_prices_uses_asof_with_tolerance(tmp_path: Path):
    ticks_path = _write_csv(tmp_path, "ticks.csv", TICK_ROWS)
    signals_path = _write_csv(tmp_path, "signals.csv", SIGNAL_ROWS)

    result = align_signals_to_prices(ticks_path, signals_path)

    assert _columns(result) == [
        "symbol",
        "timestamp",
        "close",
        "daily_return",
        "signal",
        "confidence",
        "score",
    ]
    assert _simplified_records(result) == [
        {"symbol": "AAA", "timestamp": "2025-01-01", "close": 101.0, "daily_return": 0.0, "signal": "buy", "confidence": 0.5, "score": 0.0},
        {"symbol": "AAA", "timestamp": "2025-01-02", "close": 103.0, "daily_return": 0.0198, "signal": "buy", "confidence": 0.5, "score": 0.0099},
        {"symbol": "AAA", "timestamp": "2025-01-03", "close": 103.0, "daily_return": 0.0, "signal": "sell", "confidence": 0.8, "score": 0.0},
        {"symbol": "AAA", "timestamp": "2025-01-04", "close": 107.0, "daily_return": 0.0388, "signal": "sell", "confidence": 0.8, "score": 0.031},
        {"symbol": "BBB", "timestamp": "2025-01-01", "close": 50.0, "daily_return": 0.0, "signal": "hold", "confidence": 0.0, "score": 0.0},
        {"symbol": "BBB", "timestamp": "2025-01-02", "close": 50.0, "daily_return": 0.0, "signal": "buy", "confidence": 0.0, "score": 0.0},
        {"symbol": "BBB", "timestamp": "2025-01-03", "close": 49.0, "daily_return": -0.02, "signal": "buy", "confidence": 0.0, "score": -0.0},
        {"symbol": "BBB", "timestamp": "2025-01-04", "close": 51.0, "daily_return": 0.0408, "signal": "hold", "confidence": 0.0, "score": 0.0},
    ]


def test_signal_performance_summary_groups_actionable_signals(tmp_path: Path):
    ticks_path = _write_csv(tmp_path, "ticks.csv", TICK_ROWS)
    signals_path = _write_csv(tmp_path, "signals.csv", SIGNAL_ROWS)

    result = signal_performance_summary(ticks_path, signals_path)

    assert _simplified_records(result) == [
        {"symbol": "AAA", "signal": "buy", "observations": 2, "average_score": 0.005, "total_signed_return": 0.0198},
        {"symbol": "AAA", "signal": "sell", "observations": 2, "average_score": 0.0155, "total_signed_return": -0.0388},
        {"symbol": "BBB", "signal": "buy", "observations": 2, "average_score": 0.0, "total_signed_return": -0.02},
    ]
