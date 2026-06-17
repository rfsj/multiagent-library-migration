"""Run the Planner/Diagnosis agent alone, repeated across a config x task
matrix, to measure plan validity in isolation.

Unlike `run_evaluation_matrix.py` (full workflow: planner + migration +
validation, the right tool to measure end-to-end success/pass@k), this
script only calls `eval_planner_only.py`. It is cheap (1-2 LLM calls per
attempt, no migration edits, no tests run) and is the right tool when you want
to know whether the *planner* produced a valid plan according to the benchmark
contract: expected files/symbols covered, no out-of-scope allowed files,
required dependency action planned, and basic ordering constraints satisfied.

Because the planner LLM call is not pinned to a fixed plan structure, running
the same (config, task) pair multiple times also surfaces *plan-structure
variance*: step count and symbol grouping can differ across attempts even
though the plan remains valid. That variance is itself one of the things this
driver is meant to measure.

Usage:
    .venv/bin/python scripts/run_planner_matrix.py \\
        --tasks task_002_complex_pandas_ops \\
        --configs v3_base,v3_symbol_analysis \\
        --attempts 3

    .venv/bin/python scripts/run_planner_matrix.py --list-configs
    .venv/bin/python scripts/run_planner_matrix.py --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from statistics import mean
from typing import Any

from experiment_utils import (
    ROOT,
    pass_at_k,
    pass_caret_k,
    print_json,
    utc_timestamp,
    write_json,
)
from run_evaluation_matrix import CONFIGS, _resolve_configs, _resolve_tasks

RUN_LEVEL_COLUMNS = [
    "task_id",
    "config",
    "attempt",
    "valid_plan",
    "status",
    "plan_validity_score",
    "file_coverage_rate",
    "affected_file_coverage_rate",
    "scope_precision_rate",
    "symbol_coverage_rate",
    "expected_step_coverage_rate",
    "dependency_update_required",
    "dependency_plan_valid",
    "step_order_valid",
    "human_review_match",
    "granularity_valid",
    "duplicate_step_scope_count",
    "missing_affected_source_files",
    "missing_allowed_source_files",
    "unexpected_allowed_files",
    "missing_required_symbols",
    "plan_violations",
    "migration_step_count",
    "human_review_required",
    "affected_source_files",
    "llm_calls",
    "duration_seconds",
    "planner_warnings",
    "run_dir",
]

PASS_AT_K_COLUMNS = [
    "task_id",
    "config",
    "attempts",
    "valid_plan_rate",
    "planner_pass@1",
    "planner_pass@3",
    "planner_pass@5",
    "planner_pass^1",
    "planner_pass^3",
    "planner_pass^5",
    "first_success_rank",
    "llm_calls_to_success",
]

ABLATION_COLUMNS = [
    "config",
    "tasks",
    "attempts",
    "valid_plan_rate",
    "planner_pass@3",
    "planner_pass@5",
    "avg_plan_validity_score",
    "file_coverage_rate",
    "symbol_coverage_rate",
    "expected_step_coverage_rate",
    "scope_violation_rate",
    "dependency_plan_valid_rate",
    "step_order_valid_rate",
    "human_review_match_rate",
    "granularity_valid_rate",
    "human_review_rate",
    "step_count_min",
    "step_count_max",
    "step_count_mean",
    "avg_llm_calls",
    "avg_duration_seconds",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the planner alone across a config x task matrix to validate planning behavior."
    )
    parser.add_argument(
        "--tasks",
        default=None,
        help="Comma-separated task ids. Default: every task under benchmark/.",
    )
    parser.add_argument(
        "--configs",
        default=",".join(CONFIGS.keys()),
        help=f"Comma-separated config names. Available: {', '.join(CONFIGS.keys())}.",
    )
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument(
        "--k",
        default="1,3,5",
        help="Comma-separated K values for planner pass@k/pass^k, e.g. 1,3,5.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the matrix and exit without running anything.",
    )
    parser.add_argument(
        "--list-configs",
        action="store_true",
        help="Print available config names and their env overrides, then exit.",
    )
    args = parser.parse_args()

    if args.list_configs:
        print_json(CONFIGS)
        return 0

    task_ids = _resolve_tasks(args.tasks)
    config_names = _resolve_configs(args.configs)
    k_values = _parse_k_values(args.k)

    if args.dry_run:
        print(
            f"{len(config_names)} config(s) x {len(task_ids)} task(s) "
            f"x {args.attempts} attempt(s) = "
            f"{len(config_names) * len(task_ids) * args.attempts} planner-only run(s)"
        )
        for config_name in config_names:
            for task_id in task_ids:
                print(f"  [{config_name}] {task_id}")
        return 0

    started = time.perf_counter()
    matrix_dir = (
        ROOT / "experiments" / "evaluations" / f"planner_matrix_{utc_timestamp()}"
    )
    matrix_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for config_name in config_names:
        for task_id in task_ids:
            for attempt in range(1, args.attempts + 1):
                print(
                    f"=== config={config_name} task={task_id} attempt={attempt} ===",
                    file=sys.stderr,
                )
                result = _run_planner_only(task_id, config_name)
                result["config"] = config_name
                result["attempt"] = attempt
                results.append(result)

    run_rows = _build_run_level_rows(results)
    pass_at_k_rows = _build_pass_at_k_rows(results, k_values)
    ablation_rows = _build_ablation_rows(results)

    _write_csv(matrix_dir / "planner_run_level.csv", RUN_LEVEL_COLUMNS, run_rows)
    _write_csv(matrix_dir / "planner_pass_at_k.csv", PASS_AT_K_COLUMNS, pass_at_k_rows)
    _write_csv(matrix_dir / "planner_ablation.csv", ABLATION_COLUMNS, ablation_rows)

    summary = {
        "phase": "planner_matrix",
        "configs": config_names,
        "tasks": task_ids,
        "attempts_per_run": args.attempts,
        "k": args.k,
        "matrix_dir": str(matrix_dir),
        "run_level_csv": str(matrix_dir / "planner_run_level.csv"),
        "pass_at_k_csv": str(matrix_dir / "planner_pass_at_k.csv"),
        "ablation_csv": str(matrix_dir / "planner_ablation.csv"),
        "results": results,
        "duration_seconds": round(time.perf_counter() - started, 3),
    }
    write_json(matrix_dir / "planner_matrix_report.json", summary)
    print_json({key: value for key, value in summary.items() if key != "results"})
    return 0


def _run_planner_only(task_id: str, config_name: str) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(CONFIGS[config_name])
    cmd = [sys.executable, str(ROOT / "scripts" / "eval_planner_only.py"), task_id]
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
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
            "status": "failed",
            "error": "eval_planner_only.py did not print a JSON report.",
        }
    try:
        return json.loads(proc.stdout[start:])
    except json.JSONDecodeError:
        return {
            "task_id": task_id,
            "status": "failed",
            "error": "eval_planner_only.py printed invalid JSON.",
        }


def _build_run_level_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for result in results:
        rows.append(
            {
                "task_id": result.get("task_id"),
                "config": result.get("config"),
                "attempt": result.get("attempt"),
                "valid_plan": result.get("valid_plan"),
                "status": result.get("status"),
                "plan_validity_score": result.get("plan_validity_score"),
                "file_coverage_rate": result.get("file_coverage_rate"),
                "affected_file_coverage_rate": result.get(
                    "affected_file_coverage_rate"
                ),
                "scope_precision_rate": result.get("scope_precision_rate"),
                "symbol_coverage_rate": result.get("symbol_coverage_rate"),
                "expected_step_coverage_rate": result.get(
                    "expected_step_coverage_rate"
                ),
                "dependency_update_required": result.get("dependency_update_required"),
                "dependency_plan_valid": result.get("dependency_plan_valid"),
                "step_order_valid": result.get("step_order_valid"),
                "human_review_match": result.get("human_review_match"),
                "granularity_valid": result.get("granularity_valid"),
                "duplicate_step_scope_count": result.get("duplicate_step_scope_count"),
                "missing_affected_source_files": ";".join(
                    result.get("missing_affected_source_files", []) or []
                ),
                "missing_allowed_source_files": ";".join(
                    result.get("missing_allowed_source_files", []) or []
                ),
                "unexpected_allowed_files": ";".join(
                    result.get("unexpected_allowed_files", []) or []
                ),
                "missing_required_symbols": _format_mapping(
                    result.get("missing_required_symbols", {}) or {}
                ),
                "plan_violations": _format_violations(
                    result.get("plan_violations", []) or []
                ),
                "migration_step_count": result.get("migration_step_count"),
                "human_review_required": result.get("human_review_required"),
                "affected_source_files": ";".join(
                    result.get("affected_source_files", []) or []
                ),
                "llm_calls": (result.get("llm_calls") or {}).get("total"),
                "duration_seconds": result.get("duration_seconds"),
                "planner_warnings": " | ".join(
                    result.get("planner_warnings", []) or []
                ),
                "run_dir": result.get("run_dir"),
            }
        )
    return rows


def _build_pass_at_k_rows(
    results: list[dict[str, Any]], k_values: list[int]
) -> list[dict[str, Any]]:
    rows = []
    for (config_name, task_id), items in _group_by_config_task(results).items():
        ordered = sorted(items, key=lambda item: item.get("attempt") or 0)
        successes = [bool(item.get("valid_plan")) for item in ordered]
        planner_pass_at_k = pass_at_k(successes, k_values)
        planner_pass_caret_k = pass_caret_k(successes, k_values)
        cost = _cost_to_success(ordered)
        rows.append(
            {
                "task_id": task_id,
                "config": config_name,
                "attempts": len(ordered),
                "valid_plan_rate": _safe_mean(
                    [1.0 if value else 0.0 for value in successes]
                ),
                "planner_pass@1": planner_pass_at_k.get("pass@1"),
                "planner_pass@3": planner_pass_at_k.get("pass@3"),
                "planner_pass@5": planner_pass_at_k.get("pass@5"),
                "planner_pass^1": planner_pass_caret_k.get("pass^1"),
                "planner_pass^3": planner_pass_caret_k.get("pass^3"),
                "planner_pass^5": planner_pass_caret_k.get("pass^5"),
                "first_success_rank": cost["first_success_rank"],
                "llm_calls_to_success": cost["llm_calls_to_first_success"],
            }
        )
    return rows


def _build_ablation_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_config: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        by_config.setdefault(result.get("config"), []).append(result)

    rows = []
    for config_name, items in by_config.items():
        pass_by_task = _build_pass_at_k_rows(items, [3, 5])
        planner_pass3_values = [
            row.get("planner_pass@3")
            for row in pass_by_task
            if row.get("planner_pass@3") is not None
        ]
        planner_pass5_values = [
            row.get("planner_pass@5")
            for row in pass_by_task
            if row.get("planner_pass@5") is not None
        ]
        valid_plans = [bool(item.get("valid_plan")) for item in items]
        validity_scores = [
            item.get("plan_validity_score")
            for item in items
            if item.get("plan_validity_score") is not None
        ]
        file_coverage_values = [
            item.get("file_coverage_rate")
            for item in items
            if item.get("file_coverage_rate") is not None
        ]
        symbol_coverage_values = [
            item.get("symbol_coverage_rate")
            for item in items
            if item.get("symbol_coverage_rate") is not None
        ]
        expected_step_coverage_values = [
            item.get("expected_step_coverage_rate")
            for item in items
            if item.get("expected_step_coverage_rate") is not None
        ]
        scope_violations = [
            bool(item.get("missing_allowed_source_files"))
            or bool(item.get("unexpected_allowed_files"))
            for item in items
        ]
        dependency_results = [
            bool(item.get("dependency_plan_valid"))
            for item in items
            if item.get("dependency_update_required")
        ]
        step_order_results = [
            bool(item.get("step_order_valid"))
            for item in items
            if item.get("step_order_valid") is not None
        ]
        human_review_matches = [
            bool(item.get("human_review_match"))
            for item in items
            if item.get("human_review_match") is not None
        ]
        granularity_results = [
            bool(item.get("granularity_valid"))
            for item in items
            if item.get("granularity_valid") is not None
        ]
        step_counts = [
            item.get("migration_step_count")
            for item in items
            if item.get("migration_step_count") is not None
        ]
        human_review = [bool(item.get("human_review_required")) for item in items]
        llm_calls = [
            (item.get("llm_calls") or {}).get("total")
            for item in items
            if (item.get("llm_calls") or {}).get("total") is not None
        ]
        durations = [
            item.get("duration_seconds")
            for item in items
            if item.get("duration_seconds") is not None
        ]
        tasks = {item.get("task_id") for item in items}
        rows.append(
            {
                "config": config_name,
                "tasks": len(tasks),
                "attempts": len(items),
                "valid_plan_rate": _safe_mean([1.0 if v else 0.0 for v in valid_plans]),
                "planner_pass@3": _safe_mean(
                    [1.0 if v else 0.0 for v in planner_pass3_values]
                ),
                "planner_pass@5": _safe_mean(
                    [1.0 if v else 0.0 for v in planner_pass5_values]
                ),
                "avg_plan_validity_score": _safe_mean(validity_scores),
                "file_coverage_rate": _safe_mean(file_coverage_values),
                "symbol_coverage_rate": _safe_mean(symbol_coverage_values),
                "expected_step_coverage_rate": _safe_mean(
                    expected_step_coverage_values
                ),
                "scope_violation_rate": _safe_mean(
                    [1.0 if v else 0.0 for v in scope_violations]
                ),
                "dependency_plan_valid_rate": _safe_mean(
                    [1.0 if v else 0.0 for v in dependency_results]
                ),
                "step_order_valid_rate": _safe_mean(
                    [1.0 if v else 0.0 for v in step_order_results]
                ),
                "human_review_match_rate": _safe_mean(
                    [1.0 if v else 0.0 for v in human_review_matches]
                ),
                "granularity_valid_rate": _safe_mean(
                    [1.0 if v else 0.0 for v in granularity_results]
                ),
                "human_review_rate": _safe_mean(
                    [1.0 if v else 0.0 for v in human_review]
                ),
                "step_count_min": min(step_counts) if step_counts else None,
                "step_count_max": max(step_counts) if step_counts else None,
                "step_count_mean": _safe_mean(step_counts),
                "avg_llm_calls": _safe_mean(llm_calls),
                "avg_duration_seconds": _safe_mean(durations),
            }
        )
    return rows


def _group_by_config_task(
    results: list[dict[str, Any]],
) -> dict[tuple[str | None, str | None], list[dict[str, Any]]]:
    grouped: dict[tuple[str | None, str | None], list[dict[str, Any]]] = {}
    for result in results:
        grouped.setdefault((result.get("config"), result.get("task_id")), []).append(
            result
        )
    return grouped


def _cost_to_success(items: list[dict[str, Any]]) -> dict[str, Any]:
    total_llm_calls = 0
    for index, item in enumerate(items, start=1):
        total_llm_calls += (item.get("llm_calls") or {}).get("total") or 0
        if item.get("valid_plan"):
            return {
                "first_success_rank": index,
                "llm_calls_to_first_success": total_llm_calls,
            }
    return {
        "first_success_rank": None,
        "llm_calls_to_first_success": total_llm_calls,
    }


def _parse_k_values(raw: str) -> list[int]:
    values = sorted({int(part.strip()) for part in raw.split(",") if part.strip()})
    return [value for value in values if value > 0]


def _format_mapping(value: dict[str, list[str]]) -> str:
    return " | ".join(
        f"{key}:{','.join(items)}" for key, items in sorted(value.items())
    )


def _format_violations(violations: list[dict[str, Any]]) -> str:
    return " | ".join(
        ":".join(
            part
            for part in [
                str(violation.get("severity", "")),
                str(violation.get("code", "")),
                str(violation.get("path") or violation.get("file") or ""),
            ]
            if part
        )
        for violation in violations
    )


def _safe_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(mean(values), 4)


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


if __name__ == "__main__":
    raise SystemExit(main())
