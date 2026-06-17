"""Run ValidationAgent over existing migrated run directories.

This evaluates the validator in isolation against labeled runs when
``metadata.json`` provides ``validation_oracle``. Without labels, the report
still summarizes observed approval/rejection behavior but marks correctness as
unknown.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path
from statistics import mean
from typing import Any

from experiment_utils import ROOT, print_json, utc_timestamp, write_json

RUN_LEVEL_COLUMNS = [
    "task_id",
    "run_dir",
    "oracle_available",
    "expected_verdict",
    "observed_verdict",
    "validation_decision_correct",
    "false_accept",
    "false_reject",
    "rejection_reason_match",
    "expected_rejection_reasons",
    "observed_rejection_reasons",
    "tests_passed",
    "final_validation_status",
    "out_of_scope_changes",
    "old_imports_remaining",
    "unmigrated_uses",
    "llm_calls",
    "duration_seconds",
]

SUMMARY_COLUMNS = [
    "runs",
    "labeled_runs",
    "validation_accuracy",
    "false_accept_rate",
    "false_reject_rate",
    "rejection_reason_match_rate",
    "approval_rate",
    "rejection_rate",
    "avg_llm_calls",
    "avg_duration_seconds",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run validation-only evaluation over existing run directories."
    )
    parser.add_argument(
        "--runs",
        required=True,
        help=(
            "Comma-separated run directories, a text file with one run directory "
            "per line, or an evaluation matrix directory with run_level.csv."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run_dirs = _resolve_run_dirs(args.runs)
    if args.dry_run:
        print(f"{len(run_dirs)} validation-only run(s)")
        for run_dir in run_dirs:
            print(f"  {run_dir}")
        return 0

    started = time.perf_counter()
    matrix_dir = (
        ROOT / "experiments" / "evaluations" / f"validation_matrix_{utc_timestamp()}"
    )
    matrix_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for run_dir in run_dirs:
        print(f"=== run_dir={run_dir} ===", file=sys.stderr)
        results.append(_run_validation_only(run_dir))

    run_rows = _build_run_level_rows(results)
    summary_rows = [_build_summary_row(results)]
    _write_csv(matrix_dir / "validation_run_level.csv", RUN_LEVEL_COLUMNS, run_rows)
    _write_csv(matrix_dir / "validation_summary.csv", SUMMARY_COLUMNS, summary_rows)

    summary = {
        "phase": "validation_matrix",
        "runs": [str(path) for path in run_dirs],
        "matrix_dir": str(matrix_dir),
        "run_level_csv": str(matrix_dir / "validation_run_level.csv"),
        "summary_csv": str(matrix_dir / "validation_summary.csv"),
        "results": results,
        "duration_seconds": round(time.perf_counter() - started, 3),
    }
    write_json(matrix_dir / "validation_matrix_report.json", summary)
    print_json({key: value for key, value in summary.items() if key != "results"})
    return 0


def _resolve_run_dirs(raw: str) -> list[Path]:
    candidate = Path(raw)
    if candidate.exists() and candidate.is_dir():
        run_level = candidate / "run_level.csv"
        migration_run_level = candidate / "migration_run_level.csv"
        if run_level.exists():
            return _run_dirs_from_csv(run_level)
        if migration_run_level.exists():
            return _run_dirs_from_csv(migration_run_level)
        return [candidate.resolve()]
    if candidate.exists() and candidate.is_file():
        return [
            Path(line.strip()).resolve()
            for line in candidate.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    return [Path(part.strip()).resolve() for part in raw.split(",") if part.strip()]


def _run_dirs_from_csv(path: Path) -> list[Path]:
    run_dirs = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            run_dir = row.get("run_dir")
            if run_dir:
                run_dirs.append(Path(run_dir).resolve())
    return run_dirs


def _run_validation_only(run_dir: Path) -> dict[str, Any]:
    task_id = _task_id_from_run_dir(run_dir)
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "eval_validation_only.py"),
        str(run_dir),
    ]
    if task_id:
        cmd.extend(["--task-id", task_id])
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    start = proc.stdout.find("{")
    if start == -1:
        return {
            "task_id": task_id,
            "run_dir": str(run_dir),
            "status": "failed",
            "error": "eval_validation_only.py did not print a JSON report.",
        }
    try:
        return json.loads(proc.stdout[start:])
    except json.JSONDecodeError:
        return {
            "task_id": task_id,
            "run_dir": str(run_dir),
            "status": "failed",
            "error": "eval_validation_only.py printed invalid JSON.",
        }


def _build_run_level_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for result in results:
        metrics = result.get("validation_metrics") or {}
        rows.append(
            {
                "task_id": result.get("task_id"),
                "run_dir": result.get("run_dir"),
                "oracle_available": metrics.get("oracle_available"),
                "expected_verdict": metrics.get("expected_verdict"),
                "observed_verdict": metrics.get("observed_verdict"),
                "validation_decision_correct": metrics.get(
                    "validation_decision_correct"
                ),
                "false_accept": metrics.get("false_accept"),
                "false_reject": metrics.get("false_reject"),
                "rejection_reason_match": metrics.get("rejection_reason_match"),
                "expected_rejection_reasons": ";".join(
                    metrics.get("expected_rejection_reasons") or []
                ),
                "observed_rejection_reasons": ";".join(
                    metrics.get("observed_rejection_reasons") or []
                ),
                "tests_passed": metrics.get("tests_passed"),
                "final_validation_status": metrics.get("final_validation_status"),
                "out_of_scope_changes": metrics.get("out_of_scope_changes"),
                "old_imports_remaining": metrics.get("old_imports_remaining"),
                "unmigrated_uses": metrics.get("unmigrated_uses"),
                "llm_calls": (result.get("llm_calls") or {}).get("total"),
                "duration_seconds": result.get("duration_seconds"),
            }
        )
    return rows


def _build_summary_row(results: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = [result.get("validation_metrics") or {} for result in results]
    labeled = [item for item in metrics if item.get("oracle_available")]
    return {
        "runs": len(results),
        "labeled_runs": len(labeled),
        "validation_accuracy": _mean_bool(labeled, "validation_decision_correct"),
        "false_accept_rate": _mean_bool(labeled, "false_accept"),
        "false_reject_rate": _mean_bool(labeled, "false_reject"),
        "rejection_reason_match_rate": _mean_bool(
            [
                item
                for item in labeled
                if item.get("rejection_reason_match") is not None
            ],
            "rejection_reason_match",
        ),
        "approval_rate": _safe_mean(
            [
                1.0 if item.get("observed_verdict") == "approved" else 0.0
                for item in metrics
                if item.get("observed_verdict")
            ]
        ),
        "rejection_rate": _safe_mean(
            [
                1.0 if item.get("observed_verdict") == "rejected" else 0.0
                for item in metrics
                if item.get("observed_verdict")
            ]
        ),
        "avg_llm_calls": _safe_mean(
            [
                (result.get("llm_calls") or {}).get("total")
                for result in results
                if (result.get("llm_calls") or {}).get("total") is not None
            ]
        ),
        "avg_duration_seconds": _safe_mean(
            [
                result.get("duration_seconds")
                for result in results
                if result.get("duration_seconds") is not None
            ]
        ),
    }


def _task_id_from_run_dir(run_dir: Path) -> str | None:
    report_path = run_dir / "report.json"
    if report_path.exists():
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        if payload.get("task_id"):
            return str(payload["task_id"])
    marker = "_202"
    if marker in run_dir.name:
        return run_dir.name.split(marker, 1)[0]
    return None


def _mean_bool(items: list[dict[str, Any]], key: str) -> float | None:
    values = [item.get(key) for item in items if item.get(key) is not None]
    return _safe_mean([1.0 if value else 0.0 for value in values])


def _safe_mean(values: list[Any]) -> float | None:
    numeric = [float(value) for value in values if value is not None]
    if not numeric:
        return None
    return round(mean(numeric), 4)


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


if __name__ == "__main__":
    raise SystemExit(main())
