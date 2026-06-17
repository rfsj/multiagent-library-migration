from __future__ import annotations

import argparse
import time
from pathlib import Path

from experiment_utils import (
    allowed_files_from_diagnosis,
    configure_llm_logging,
    env_snapshot,
    llm_call_summary,
    print_json,
    read_json,
    write_json,
)
from src.agents.validation_agent import ValidationAgent
from src.tools.test_runner import run_pytest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run only the Validation agent against an existing run directory."
    )
    parser.add_argument(
        "run_dir",
        type=Path,
        help="Existing run dir containing project/, snapshots/before_migration/, and logs/diagnosis_plan.json.",
    )
    parser.add_argument("--source-library", default=None)
    args = parser.parse_args()

    started = time.perf_counter()
    run_dir = args.run_dir.resolve()
    project_dir = run_dir / "project"
    before_dir = run_dir / "snapshots" / "before_migration"
    logs_dir = run_dir / "logs"
    diagnosis_path = logs_dir / "diagnosis_plan.json"

    if not project_dir.exists():
        raise FileNotFoundError(f"Missing project directory: {project_dir}")
    if not before_dir.exists():
        raise FileNotFoundError(f"Missing before snapshot: {before_dir}")
    if not diagnosis_path.exists():
        raise FileNotFoundError(f"Missing diagnosis plan: {diagnosis_path}")

    configure_llm_logging(logs_dir)
    diagnosis = read_json(diagnosis_path)
    source_library = args.source_library or diagnosis.get("source_library")
    if not source_library:
        raise RuntimeError("source_library not found; pass --source-library.")

    tests = run_pytest(project_dir, logs_dir / "validation_only_pytest.log")
    final_validation = ValidationAgent().final_validate(
        project_dir,
        before_dir,
        logs_dir,
        source_library,
        allowed_files=allowed_files_from_diagnosis(diagnosis),
    )
    output = {
        "phase": "validation_only",
        "status": (
            "success"
            if tests["passed"] and final_validation.get("status") == "approved"
            else "failed"
        ),
        "run_dir": str(run_dir),
        "project_dir": str(project_dir),
        "before_dir": str(before_dir),
        "logs_dir": str(logs_dir),
        "diagnosis_plan": str(diagnosis_path),
        "tests": tests,
        "final_validation": final_validation,
        "allowed_files": allowed_files_from_diagnosis(diagnosis),
        "llm_calls": llm_call_summary(),
        "environment": env_snapshot(),
        "duration_seconds": round(time.perf_counter() - started, 3),
    }
    write_json(run_dir / "validation_only_report.json", output)
    print_json(output)
    return 0 if output["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
