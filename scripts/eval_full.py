from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from experiment_utils import (
    ROOT,
    cost_to_success,
    env_snapshot,
    pass_at_k,
    pass_caret_k,
    print_json,
    read_json,
    success_from_report,
    utc_timestamp,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the full workflow one or more times and compute execution-based/pass@k metrics."
    )
    parser.add_argument("task_id")
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument(
        "--k",
        default="1,3,5",
        help="Comma-separated K values for pass@k/pass^k, e.g. 1,3,5.",
    )
    args = parser.parse_args()

    started = time.perf_counter()
    k_values = _parse_k_values(args.k)
    reports: list[dict] = []
    attempts = []

    for attempt in range(1, args.attempts + 1):
        proc = _run_task(args.task_id, args.skip_install)
        report = _extract_report(proc.stdout)
        if report is None:
            report = {
                "task_id": args.task_id,
                "status": "failed",
                "run_dir": None,
                "logs_dir": None,
                "llm_calls": {"total": None, "by_label": {}},
                "error": "run_task.py did not print a JSON report.",
            }
        reports.append(report)
        attempts.append({
            "attempt": attempt,
            "returncode": proc.returncode,
            "success": success_from_report(report),
            "status": report.get("status"),
            "run_dir": report.get("run_dir"),
            "logs_dir": report.get("logs_dir"),
            "tests_before": report.get("tests_before"),
            "tests_after": report.get("tests_after"),
            "final_validation_status": _final_validation_status(report),
            "out_of_scope_changes": report.get("out_of_scope_changes"),
            "unmigrated_uses": report.get("unmigrated_uses"),
            "total_retries": report.get("total_retries"),
            "replan_count": report.get("replan_count"),
            "llm_calls": report.get("llm_calls", {}),
        })

    successes = [attempt["success"] for attempt in attempts]
    output = {
        "phase": "full_workflow",
        "task_id": args.task_id,
        "attempts_requested": args.attempts,
        "attempts_completed": len(attempts),
        "success_count": sum(1 for success in successes if success),
        "success_rate": (
            sum(1 for success in successes if success) / len(successes)
            if successes
            else 0
        ),
        "pass_at_k": pass_at_k(successes, k_values),
        "pass_caret_k": pass_caret_k(successes, k_values),
        "cost_to_success": cost_to_success(reports),
        "attempts": attempts,
        "environment": env_snapshot(),
        "duration_seconds": round(time.perf_counter() - started, 3),
    }

    eval_dir = ROOT / "experiments" / "evaluations"
    eval_dir.mkdir(parents=True, exist_ok=True)
    output_path = eval_dir / f"{args.task_id}_{utc_timestamp()}_full_eval.json"
    output["evaluation_report"] = str(output_path)
    write_json(output_path, output)
    print_json(output)
    return 0 if output["success_count"] > 0 else 1


def _run_task(task_id: str, skip_install: bool) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(ROOT / "scripts" / "run_task.py"), task_id]
    if skip_install:
        cmd.append("--skip-install")
    return subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _extract_report(stdout: str) -> dict | None:
    start = stdout.find("{")
    if start == -1:
        return None
    try:
        report = json.loads(stdout[start:])
    except json.JSONDecodeError:
        return None
    run_dir = report.get("run_dir")
    if run_dir:
        report_path = Path(run_dir) / "report.json"
        if report_path.exists():
            return read_json(report_path)
    return report


def _final_validation_status(report: dict) -> str | None:
    run_dir = report.get("run_dir")
    if not run_dir:
        return None
    path = Path(run_dir) / "logs" / "final_validation.json"
    if not path.exists():
        return None
    return read_json(path).get("status")


def _parse_k_values(raw: str) -> list[int]:
    values = sorted({int(part.strip()) for part in raw.split(",") if part.strip()})
    return [value for value in values if value > 0]


if __name__ == "__main__":
    raise SystemExit(main())
