"""Driver for the full evaluation plan in docs/experiment_evaluation_plan.md.

Loops the full workflow (`eval_full.py`) over a matrix of (config, task)
pairs, then aggregates the per-attempt results into the three result tables
described in the plan: Run-Level, Pass@K, and Ablation.

IMPORTANT — config-to-flag mapping caveat:

The evaluation plan lists conditions ("V3 base", "+symbol analysis",
"+least-scope", "+guardrails", "+human-review-signal") as if each were
independently switchable. In the current `PlannerV3Agent` implementation that
is only partially true:

- `PLANNER_USE_SYMBOL_ANALYSIS` is the planner-side flag: it turns on symbol
  analysis, least-scope planning, and `dataframe_flow_analysis` together —
  there is no separate flag to turn any one of those off while keeping the
  others on.
- The matching *migration-side* enforcement for narrow `allowed_symbols`
  steps (clip the LLM's output back to the allowed symbol; deterministically
  drop the source-library import once no symbol in the file still needs it)
  lives in the migration agent, not the planner, and is gated by its own flag:
  `enforce_symbol_scope` / `MIGRATION_USE_SCOPE`. Turning on planner-side
  symbol analysis without this produces steps that are scoped on paper only —
  the LLM is free to ignore `allowed_symbols`, which is what caused the
  `old_imports_remaining` rejections seen in early runs of this matrix (see
  experiments/evaluations/matrix_20260616T201117Z). `v3_symbol_analysis` below
  pairs both flags so the config tests the coherent combination.
- "Deterministic guardrails" and the "human-review-signal" consolidation are
  unconditional in v3: every plan goes through them, with no off-switch.

So this driver only varies flags that actually exist. It exposes three
named configs (see CONFIGS below): `v3_base`, `v3_symbol_analysis` (symbol
analysis + least-scope + flow-grouping + matching migration-side scope
enforcement), and `legacy` (the pre-v3 diagnosis agent, for reference).
Guardrails/least-scope/human-review-signal/flow-grouping show up as "always
on for any v3_* config" rather than as separate columns.

This issues real LLM calls (every attempt runs the full workflow end to end).
Use --dry-run first to see the matrix size, and start with a small --tasks /
--attempts selection.

Usage:
    .venv/bin/python scripts/run_evaluation_matrix.py \\
        --tasks task_001_read_csv_filter,task_020_full_analytics_pipeline \\
        --configs v3_base,v3_symbol_analysis \\
        --attempts 3 --k 1,3

    .venv/bin/python scripts/run_evaluation_matrix.py --list-configs
    .venv/bin/python scripts/run_evaluation_matrix.py --dry-run
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
from typing import Any

from experiment_utils import ROOT, print_json, utc_timestamp, write_json

CONFIGS: dict[str, dict[str, str]] = {
    "v3_base": {
        "DIAGNOSIS_AGENT_IMPL": "v3",
        "PLANNER_USE_SYMBOL_ANALYSIS": "0",
    },
    "v3_symbol_analysis": {
        "DIAGNOSIS_AGENT_IMPL": "v3",
        "PLANNER_USE_SYMBOL_ANALYSIS": "1",
        "MIGRATION_USE_SCOPE": "1",
    },
    "legacy": {
        "DIAGNOSIS_AGENT_IMPL": "legacy",
    },
}

RUN_LEVEL_COLUMNS = [
    "task_id",
    "config",
    "attempt",
    "success",
    "tests_after",
    "final_validation",
    "out_of_scope_changes",
    "unmigrated_uses",
    "retries",
    "replans",
    "llm_calls",
    "run_dir",
]

PASS_AT_K_COLUMNS = [
    "task_id",
    "config",
    "attempts",
    "success_rate",
    "pass@1",
    "pass@3",
    "pass@5",
    "pass^1",
    "pass^3",
    "pass^5",
    "first_success_rank",
    "llm_calls_to_success",
]

ABLATION_COLUMNS = [
    "config",
    "tasks",
    "success_rate",
    "pass@3",
    "pass@5",
    "avg_llm_calls",
    "avg_retries",
    "scope_violation_rate",
    "unmigrated_usage_rate",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the full evaluation plan across a config x task matrix."
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
    parser.add_argument("--k", default="1,3")
    parser.add_argument("--skip-install", action="store_true")
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

    if args.dry_run:
        print(
            f"{len(config_names)} config(s) x {len(task_ids)} task(s) "
            f"x {args.attempts} attempt(s) = "
            f"{len(config_names) * len(task_ids) * args.attempts} full-workflow run(s)"
        )
        for config_name in config_names:
            for task_id in task_ids:
                print(f"  [{config_name}] {task_id}")
        return 0

    started = time.perf_counter()
    matrix_dir = ROOT / "experiments" / "evaluations" / f"matrix_{utc_timestamp()}"
    matrix_dir.mkdir(parents=True, exist_ok=True)

    full_evals: list[dict[str, Any]] = []
    for config_name in config_names:
        for task_id in task_ids:
            print(f"=== config={config_name} task={task_id} ===", file=sys.stderr)
            full_eval = _run_eval_full(
                task_id=task_id,
                config_name=config_name,
                attempts=args.attempts,
                k=args.k,
                skip_install=args.skip_install,
            )
            full_eval["config"] = config_name
            full_evals.append(full_eval)

    run_level_rows = _build_run_level_rows(full_evals)
    pass_at_k_rows = _build_pass_at_k_rows(full_evals)
    ablation_rows = _build_ablation_rows(full_evals)

    _write_csv(matrix_dir / "run_level.csv", RUN_LEVEL_COLUMNS, run_level_rows)
    _write_csv(matrix_dir / "pass_at_k.csv", PASS_AT_K_COLUMNS, pass_at_k_rows)
    _write_csv(matrix_dir / "ablation.csv", ABLATION_COLUMNS, ablation_rows)

    summary = {
        "phase": "evaluation_matrix",
        "configs": config_names,
        "tasks": task_ids,
        "attempts_per_run": args.attempts,
        "k": args.k,
        "matrix_dir": str(matrix_dir),
        "run_level_csv": str(matrix_dir / "run_level.csv"),
        "pass_at_k_csv": str(matrix_dir / "pass_at_k.csv"),
        "ablation_csv": str(matrix_dir / "ablation.csv"),
        "full_evals": full_evals,
        "duration_seconds": round(time.perf_counter() - started, 3),
    }
    write_json(matrix_dir / "matrix_report.json", summary)
    print_json({k: v for k, v in summary.items() if k != "full_evals"})
    return 0


def _resolve_tasks(raw: str | None) -> list[str]:
    if raw:
        return [task.strip() for task in raw.split(",") if task.strip()]
    benchmark_dir = ROOT / "benchmark"
    return sorted(
        path.name
        for path in benchmark_dir.iterdir()
        if path.is_dir() and (path / "metadata.json").exists()
    )


def _resolve_configs(raw: str) -> list[str]:
    names = [name.strip() for name in raw.split(",") if name.strip()]
    unknown = [name for name in names if name not in CONFIGS]
    if unknown:
        raise SystemExit(
            f"Unknown config(s): {unknown}. Available: {', '.join(CONFIGS.keys())}"
        )
    return names


def _run_eval_full(
    *,
    task_id: str,
    config_name: str,
    attempts: int,
    k: str,
    skip_install: bool,
) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(CONFIGS[config_name])
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "eval_full.py"),
        task_id,
        "--attempts",
        str(attempts),
        "--k",
        k,
    ]
    if skip_install:
        cmd.append("--skip-install")
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
            "error": "eval_full.py did not print a JSON report.",
            "attempts": [],
        }
    try:
        return json.loads(proc.stdout[start:])
    except json.JSONDecodeError:
        return {
            "task_id": task_id,
            "status": "failed",
            "error": "eval_full.py printed invalid JSON.",
            "attempts": [],
        }


def _build_run_level_rows(full_evals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for full_eval in full_evals:
        task_id = full_eval.get("task_id")
        config_name = full_eval.get("config")
        for attempt in full_eval.get("attempts", []):
            rows.append(
                {
                    "task_id": task_id,
                    "config": config_name,
                    "attempt": attempt.get("attempt"),
                    "success": attempt.get("success"),
                    "tests_after": attempt.get("tests_after"),
                    "final_validation": attempt.get("final_validation_status"),
                    "out_of_scope_changes": attempt.get("out_of_scope_changes"),
                    "unmigrated_uses": attempt.get("unmigrated_uses"),
                    "retries": attempt.get("total_retries"),
                    "replans": attempt.get("replan_count"),
                    "llm_calls": attempt.get("llm_calls", {}).get("total"),
                    "run_dir": attempt.get("run_dir"),
                }
            )
    return rows


def _build_pass_at_k_rows(full_evals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for full_eval in full_evals:
        pass_at_k = full_eval.get("pass_at_k", {})
        pass_caret_k = full_eval.get("pass_caret_k", {})
        cost = full_eval.get("cost_to_success", {})
        rows.append(
            {
                "task_id": full_eval.get("task_id"),
                "config": full_eval.get("config"),
                "attempts": full_eval.get("attempts_completed"),
                "success_rate": full_eval.get("success_rate"),
                "pass@1": pass_at_k.get("pass@1"),
                "pass@3": pass_at_k.get("pass@3"),
                "pass@5": pass_at_k.get("pass@5"),
                "pass^1": pass_caret_k.get("pass^1"),
                "pass^3": pass_caret_k.get("pass^3"),
                "pass^5": pass_caret_k.get("pass^5"),
                "first_success_rank": cost.get("first_success_rank"),
                "llm_calls_to_success": cost.get("llm_calls_to_first_success"),
            }
        )
    return rows


def _build_ablation_rows(full_evals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_config: dict[str, list[dict[str, Any]]] = {}
    for full_eval in full_evals:
        by_config.setdefault(full_eval.get("config"), []).append(full_eval)

    rows = []
    for config_name, evals in by_config.items():
        all_attempts = [
            attempt for full_eval in evals for attempt in full_eval.get("attempts", [])
        ]
        successes = [bool(attempt.get("success")) for attempt in all_attempts]
        llm_calls = [
            attempt.get("llm_calls", {}).get("total")
            for attempt in all_attempts
            if attempt.get("llm_calls", {}).get("total") is not None
        ]
        retries = [
            attempt.get("total_retries")
            for attempt in all_attempts
            if attempt.get("total_retries") is not None
        ]
        scope_violations = [
            (attempt.get("out_of_scope_changes") or 0) > 0 for attempt in all_attempts
        ]
        unmigrated = [
            (attempt.get("unmigrated_uses") or 0) > 0 for attempt in all_attempts
        ]
        pass3_values = [
            full_eval.get("pass_at_k", {}).get("pass@3")
            for full_eval in evals
            if full_eval.get("pass_at_k", {}).get("pass@3") is not None
        ]
        pass5_values = [
            full_eval.get("pass_at_k", {}).get("pass@5")
            for full_eval in evals
            if full_eval.get("pass_at_k", {}).get("pass@5") is not None
        ]
        rows.append(
            {
                "config": config_name,
                "tasks": len(evals),
                "success_rate": _safe_mean([1.0 if s else 0.0 for s in successes]),
                "pass@3": _safe_mean([1.0 if v else 0.0 for v in pass3_values]),
                "pass@5": _safe_mean([1.0 if v else 0.0 for v in pass5_values]),
                "avg_llm_calls": _safe_mean(llm_calls),
                "avg_retries": _safe_mean(retries),
                "scope_violation_rate": _safe_mean(
                    [1.0 if v else 0.0 for v in scope_violations]
                ),
                "unmigrated_usage_rate": _safe_mean(
                    [1.0 if v else 0.0 for v in unmigrated]
                ),
            }
        )
    return rows


def _safe_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


if __name__ == "__main__":
    raise SystemExit(main())
