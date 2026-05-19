from pathlib import Path

from src.tools.project_scanner import scan_project


def test_task_001_contains_expected_pandas_usage():
    project_dir = Path("benchmark/task_001_read_csv_filter/input_project")

    scan = scan_project(project_dir)

    assert "requirements.txt" in scan["dependency_files"]
    assert "src/orders/processing.py" in scan["affected_files"]
    assert {call["api"] for call in scan["pandas_api_calls"]} >= {
        "pd.read_csv",
        "boolean_filter",
        "column_selection",
        "sort_values",
    }
