"""Recompute planner-only validity metrics for an existing planner matrix.

This does not rerun planner LLM calls. It reopens each run_dir referenced by
planner_matrix_report.json, reloads logs/diagnosis_plan.json and
logs/project_audit_eval.json, applies the current plan validator, and rewrites
planner_run_level.csv, planner_pass_at_k.csv, planner_ablation.csv, and the
matrix report results.

Usage:
    .venv/bin/python scripts/recompute_planner_matrix.py \
        experiments/evaluations/planner_matrix_<timestamp>/
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from experiment_utils import load_task_metadata, read_json, write_json
from run_planner_matrix import (
    ABLATION_COLUMNS,
    PASS_AT_K_COLUMNS,
    RUN_LEVEL_COLUMNS,
    _build_ablation_rows,
    _build_pass_at_k_rows,
    _build_run_level_rows,
    _parse_k_values,
    _write_csv,
)
from src.evaluation.plan_validator import validate_plan


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recompute planner matrix validity metrics without rerunning planner attempts."
    )
    parser.add_argument("matrix_dir", help="Existing planner_matrix_<timestamp> directory.")
    parser.add_argument(
        "--k",
        default=None,
        help="Override comma-separated K values. Default: reuse report k or 1,3,5.",
    )
    args = parser.parse_args()

    matrix_dir = Path(args.matrix_dir).resolve()
    report_path = matrix_dir / "planner_matrix_report.json"
    if not report_path.exists():
        parser.error(f"{report_path} does not exist.")

    report = read_json(report_path)
    results = [_recompute_result(result) for result in report.get("results", [])]
    k_raw = args.k or report.get("k") or "1,3,5"
    k_values = _parse_k_values(k_raw)

    _write_csv(
        matrix_dir / "planner_run_level.csv",
        RUN_LEVEL_COLUMNS,
        _build_run_level_rows(results),
    )
    _write_csv(
        matrix_dir / "planner_pass_at_k.csv",
        PASS_AT_K_COLUMNS,
        _build_pass_at_k_rows(results, k_values),
    )
    _write_csv(
        matrix_dir / "planner_ablation.csv",
        ABLATION_COLUMNS,
        _build_ablation_rows(results),
    )

    report["results"] = results
    report["k"] = k_raw
    report["run_level_csv"] = str(matrix_dir / "planner_run_level.csv")
    report["pass_at_k_csv"] = str(matrix_dir / "planner_pass_at_k.csv")
    report["ablation_csv"] = str(matrix_dir / "planner_ablation.csv")
    write_json(report_path, report)
    print(json.dumps({
        "phase": "planner_matrix_recomputed",
        "matrix_dir": str(matrix_dir),
        "k": k_raw,
        "attempts": len(results),
    }, indent=2))
    return 0


def _recompute_result(result: dict[str, Any]) -> dict[str, Any]:
    updated = dict(result)
    run_dir = result.get("run_dir")
    task_id = result.get("task_id")
    if not run_dir or not task_id:
        return updated

    logs_dir = Path(run_dir) / "logs"
    diagnosis_path = logs_dir / "diagnosis_plan.json"
    audit_path = logs_dir / "project_audit_eval.json"
    if not diagnosis_path.exists() or not audit_path.exists():
        return updated

    metadata = load_task_metadata(task_id)
    validation = validate_plan(
        read_json(diagnosis_path),
        read_json(audit_path),
        metadata.get("validity_oracle") or metadata.get("expected_planner"),
    )
    write_json(logs_dir / "plan_validation.json", validation)
    updated.update(validation)
    return updated


if __name__ == "__main__":
    raise SystemExit(main())
