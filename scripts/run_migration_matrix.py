"""Run the Migration agent against existing diagnosis plans.

This isolates migration quality from planner variance: each attempt consumes a
frozen ``logs/diagnosis_plan.json`` produced by planner-only or full runs.

Typical usage after a planner matrix:

    .venv/bin/python scripts/run_migration_matrix.py \\
        --planner-matrix experiments/evaluations/planner_matrix_<ts> \\
        --only-valid-plans
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
from run_evaluation_matrix import CONFIGS

RUN_LEVEL_COLUMNS = [
    "task_id",
    "config",
    "attempt",
    "migration_success",
    "status",
    "behavior_preserved",
    "final_validation_approved",
    "source_usage_removed",
    "old_imports_remaining",
    "unmigrated_uses",
    "target_usage_added",
    "scope_compliance",
    "out_of_scope_changes",
    "missing_required_changed_files",
    "missing_target_usage_files",
    "diff_line_count",
    "violations",
    "llm_calls",
    "duration_seconds",
    "run_dir",
    "diagnosis_plan",
]

PASS_AT_K_COLUMNS = [
    "task_id",
    "config",
    "attempts",
    "migration_success_rate",
    "migration_pass@1",
    "migration_pass@3",
    "migration_pass@5",
    "migration_pass^1",
    "migration_pass^3",
    "migration_pass^5",
    "first_success_rank",
    "llm_calls_to_success",
]

ABLATION_COLUMNS = [
    "config",
    "tasks",
    "attempts",
    "migration_success_rate",
    "migration_pass@3",
    "migration_pass@5",
    "behavior_preservation_rate",
    "scope_compliance_rate",
    "source_usage_removed_rate",
    "target_usage_added_rate",
    "avg_diff_line_count",
    "avg_llm_calls",
    "avg_duration_seconds",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run migration-only evaluation over frozen diagnosis plans."
    )
    parser.add_argument(
        "--planner-matrix",
        type=Path,
        required=True,
        help="Directory containing planner_run_level.csv with run_dir values.",
    )
    parser.add_argument("--k", default="1,3,5")
    parser.add_argument(
        "--only-valid-plans",
        action="store_true",
        help="Skip planner attempts whose valid_plan column is not true.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the selected plans and exit without running migration.",
    )
    args = parser.parse_args()

    selected = _load_planner_rows(args.planner_matrix, args.only_valid_plans)
    if args.dry_run:
        print(f"{len(selected)} migration-only run(s)")
        for row in selected:
            print(
                f"  [{row['config']}] {row['task_id']} attempt={row['attempt']} {row['diagnosis_plan']}"
            )
        return 0

    started = time.perf_counter()
    matrix_dir = (
        ROOT / "experiments" / "evaluations" / f"migration_matrix_{utc_timestamp()}"
    )
    matrix_dir.mkdir(parents=True, exist_ok=True)
    k_values = _parse_k_values(args.k)

    results = []
    for row in selected:
        print(
            f"=== config={row['config']} task={row['task_id']} attempt={row['attempt']} ===",
            file=sys.stderr,
        )
        result = _run_migration_only(
            row["task_id"],
            Path(row["diagnosis_plan"]),
            row["config"],
        )
        result["config"] = row["config"]
        result["attempt"] = int(row["attempt"])
        result["input_planner_run_dir"] = row["planner_run_dir"]
        results.append(result)

    run_rows = _build_run_level_rows(results)
    pass_rows = _build_pass_at_k_rows(results, k_values)
    ablation_rows = _build_ablation_rows(results)

    _write_csv(matrix_dir / "migration_run_level.csv", RUN_LEVEL_COLUMNS, run_rows)
    _write_csv(matrix_dir / "migration_pass_at_k.csv", PASS_AT_K_COLUMNS, pass_rows)
    _write_csv(matrix_dir / "migration_ablation.csv", ABLATION_COLUMNS, ablation_rows)

    summary = {
        "phase": "migration_matrix",
        "source_planner_matrix": str(args.planner_matrix.resolve()),
        "only_valid_plans": args.only_valid_plans,
        "k": args.k,
        "matrix_dir": str(matrix_dir),
        "run_level_csv": str(matrix_dir / "migration_run_level.csv"),
        "pass_at_k_csv": str(matrix_dir / "migration_pass_at_k.csv"),
        "ablation_csv": str(matrix_dir / "migration_ablation.csv"),
        "results": results,
        "duration_seconds": round(time.perf_counter() - started, 3),
    }
    write_json(matrix_dir / "migration_matrix_report.json", summary)
    print_json({key: value for key, value in summary.items() if key != "results"})
    return 0


def _load_planner_rows(planner_matrix: Path, only_valid: bool) -> list[dict[str, str]]:
    path = planner_matrix / "planner_run_level.csv"
    if not path.exists():
        raise SystemExit(f"Missing planner run-level CSV: {path}")
    rows = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if only_valid and str(row.get("valid_plan", "")).lower() != "true":
                continue
            run_dir = row.get("run_dir")
            if not run_dir:
                continue
            diagnosis_plan = Path(run_dir) / "logs" / "diagnosis_plan.json"
            if not diagnosis_plan.exists():
                continue
            rows.append(
                {
                    "task_id": row["task_id"],
                    "config": row["config"],
                    "attempt": row["attempt"],
                    "planner_run_dir": run_dir,
                    "diagnosis_plan": str(diagnosis_plan),
                }
            )
    return rows


def _run_migration_only(
    task_id: str, diagnosis_plan: Path, config: str
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "eval_migration_only.py"),
        task_id,
        "--diagnosis-plan",
        str(diagnosis_plan),
    ]
    env = os.environ.copy()
    env.update(CONFIGS.get(config, {}))
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
            "error": "eval_migration_only.py did not print a JSON report.",
            "input_diagnosis_plan": str(diagnosis_plan),
        }
    try:
        return json.loads(proc.stdout[start:])
    except json.JSONDecodeError:
        return {
            "task_id": task_id,
            "status": "failed",
            "error": "eval_migration_only.py printed invalid JSON.",
            "input_diagnosis_plan": str(diagnosis_plan),
        }


def _build_run_level_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for result in results:
        metrics = result.get("migration_metrics") or {}
        rows.append(
            {
                "task_id": result.get("task_id"),
                "config": result.get("config"),
                "attempt": result.get("attempt"),
                "migration_success": metrics.get("migration_success"),
                "status": result.get("status"),
                "behavior_preserved": metrics.get("behavior_preserved"),
                "final_validation_approved": metrics.get("final_validation_approved"),
                "source_usage_removed": metrics.get("source_usage_removed"),
                "old_imports_remaining": metrics.get("old_imports_remaining"),
                "unmigrated_uses": metrics.get("unmigrated_uses"),
                "target_usage_added": metrics.get("target_usage_added"),
                "scope_compliance": metrics.get("scope_compliance"),
                "out_of_scope_changes": metrics.get("out_of_scope_changes"),
                "missing_required_changed_files": ";".join(
                    metrics.get("missing_required_changed_files") or []
                ),
                "missing_target_usage_files": ";".join(
                    metrics.get("missing_target_usage_files") or []
                ),
                "diff_line_count": metrics.get("diff_line_count"),
                "violations": ";".join(metrics.get("violations") or []),
                "llm_calls": (result.get("llm_calls") or {}).get("total"),
                "duration_seconds": result.get("duration_seconds"),
                "run_dir": result.get("run_dir"),
                "diagnosis_plan": result.get("input_diagnosis_plan"),
            }
        )
    return rows


def _build_pass_at_k_rows(
    results: list[dict[str, Any]], k_values: list[int]
) -> list[dict[str, Any]]:
    rows = []
    for (config, task_id), items in _group_by_config_task(results).items():
        ordered = sorted(items, key=lambda item: item.get("attempt") or 0)
        successes = [
            bool((item.get("migration_metrics") or {}).get("migration_success"))
            for item in ordered
        ]
        p_at_k = pass_at_k(successes, k_values)
        p_caret_k = pass_caret_k(successes, k_values)
        cost = _cost_to_success(ordered)
        rows.append(
            {
                "task_id": task_id,
                "config": config,
                "attempts": len(ordered),
                "migration_success_rate": _safe_mean(
                    [1.0 if s else 0.0 for s in successes]
                ),
                "migration_pass@1": p_at_k.get("pass@1"),
                "migration_pass@3": p_at_k.get("pass@3"),
                "migration_pass@5": p_at_k.get("pass@5"),
                "migration_pass^1": p_caret_k.get("pass^1"),
                "migration_pass^3": p_caret_k.get("pass^3"),
                "migration_pass^5": p_caret_k.get("pass^5"),
                "first_success_rank": cost["first_success_rank"],
                "llm_calls_to_success": cost["llm_calls_to_first_success"],
            }
        )
    return rows


def _build_ablation_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    by_config: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        by_config.setdefault(result.get("config"), []).append(result)
    for config, items in by_config.items():
        pass_rows = _build_pass_at_k_rows(items, [3, 5])
        metrics = [item.get("migration_metrics") or {} for item in items]
        rows.append(
            {
                "config": config,
                "tasks": len({item.get("task_id") for item in items}),
                "attempts": len(items),
                "migration_success_rate": _mean_bool(metrics, "migration_success"),
                "migration_pass@3": _safe_mean(
                    [
                        1.0 if row.get("migration_pass@3") else 0.0
                        for row in pass_rows
                        if row.get("migration_pass@3") is not None
                    ]
                ),
                "migration_pass@5": _safe_mean(
                    [
                        1.0 if row.get("migration_pass@5") else 0.0
                        for row in pass_rows
                        if row.get("migration_pass@5") is not None
                    ]
                ),
                "behavior_preservation_rate": _mean_bool(metrics, "behavior_preserved"),
                "scope_compliance_rate": _mean_bool(metrics, "scope_compliance"),
                "source_usage_removed_rate": _mean_bool(
                    metrics, "source_usage_removed"
                ),
                "target_usage_added_rate": _mean_bool(metrics, "target_usage_added"),
                "avg_diff_line_count": _safe_mean(
                    [
                        m.get("diff_line_count")
                        for m in metrics
                        if m.get("diff_line_count") is not None
                    ]
                ),
                "avg_llm_calls": _safe_mean(
                    [
                        (item.get("llm_calls") or {}).get("total")
                        for item in items
                        if (item.get("llm_calls") or {}).get("total") is not None
                    ]
                ),
                "avg_duration_seconds": _safe_mean(
                    [
                        item.get("duration_seconds")
                        for item in items
                        if item.get("duration_seconds") is not None
                    ]
                ),
            }
        )
    return rows


def _group_by_config_task(
    results: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for result in results:
        grouped.setdefault((result.get("config"), result.get("task_id")), []).append(
            result
        )
    return grouped


def _cost_to_success(items: list[dict[str, Any]]) -> dict[str, Any]:
    total_calls = 0
    for index, item in enumerate(items, start=1):
        total_calls += (item.get("llm_calls") or {}).get("total") or 0
        if (item.get("migration_metrics") or {}).get("migration_success"):
            return {
                "first_success_rank": index,
                "llm_calls_to_first_success": total_calls,
            }
    return {"first_success_rank": None, "llm_calls_to_first_success": total_calls}


def _mean_bool(items: list[dict[str, Any]], key: str) -> float | None:
    values = [item.get(key) for item in items if item.get(key) is not None]
    return _safe_mean([1.0 if value else 0.0 for value in values])


def _safe_mean(values: list[Any]) -> float | None:
    numeric = [float(value) for value in values if value is not None]
    if not numeric:
        return None
    return round(mean(numeric), 4)


def _parse_k_values(raw: str) -> list[int]:
    values = sorted({int(part.strip()) for part in raw.split(",") if part.strip()})
    return [value for value in values if value > 0]


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


if __name__ == "__main__":
    raise SystemExit(main())
