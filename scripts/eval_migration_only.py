from __future__ import annotations

import argparse
import time
from pathlib import Path

from experiment_utils import (
    allowed_files_from_diagnosis,
    configure_llm_logging,
    create_eval_run_dir,
    enrich_step,
    env_snapshot,
    llm_call_summary,
    load_task_metadata,
    print_json,
    read_json,
    setup_project_copy,
    write_json,
)
from src import llm_proxy
from src.agents.migration_agent import MigrationAgent
from src.agents.validation_agent import ValidationAgent
from src.evaluation.migration_validator import validate_migration_result
from src.tools.diff_analyzer import unified_diff


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run only the Migration agent using an existing diagnosis plan."
    )
    parser.add_argument("task_id")
    parser.add_argument(
        "--diagnosis-plan",
        required=True,
        type=Path,
        help="Path to logs/diagnosis_plan.json produced by planner-only or full run.",
    )
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    started = time.perf_counter()
    metadata = load_task_metadata(args.task_id)
    diagnosis = read_json(args.diagnosis_plan)
    run_dir = create_eval_run_dir(args.task_id, "migration", args.run_id)
    logs_dir = run_dir / "logs"
    project_dir, before_dir = setup_project_copy(args.task_id, run_dir)
    configure_llm_logging(logs_dir)
    write_json(logs_dir / "diagnosis_plan.json", diagnosis)

    agent = MigrationAgent()
    migrations = []
    for step in diagnosis.get("migration_steps", []):
        enriched = enrich_step(step, diagnosis)
        llm_proxy.set_label(f"migration_only:{enriched['step_id']}")
        migrations.append(agent.run_step(project_dir, enriched, logs_dir))

    final_validation = ValidationAgent().final_validate(
        project_dir,
        before_dir,
        logs_dir,
        metadata["source_library"],
        allowed_files=allowed_files_from_diagnosis(diagnosis),
    )
    tests_after = {
        "status": final_validation.get("tests"),
        "passed": final_validation.get("tests") == "passed",
    }
    migration_metrics = validate_migration_result(
        project_dir=project_dir,
        before_dir=before_dir,
        diagnosis=diagnosis,
        metadata=metadata,
        tests=tests_after,
        final_validation=final_validation,
    )

    diff_text = unified_diff(before_dir, project_dir)
    (run_dir / "diff.patch").write_text(diff_text, encoding="utf-8")

    changed_files = sorted(
        {
            file
            for migration in migrations
            for file in migration.get("changed_files", [])
        }
    )
    output = {
        "phase": "migration_only",
        "status": "success" if migration_metrics["migration_success"] else "failed",
        "task_id": args.task_id,
        "source_library": metadata["source_library"],
        "target_library": metadata["target_library"],
        "run_dir": str(run_dir),
        "project_dir": str(project_dir),
        "before_dir": str(before_dir),
        "logs_dir": str(logs_dir),
        "input_diagnosis_plan": str(args.diagnosis_plan),
        "migration_step_count": len(diagnosis.get("migration_steps", [])),
        "executed_step_count": len(migrations),
        "changed_files": changed_files,
        "tests_after": tests_after,
        "final_validation": final_validation,
        "migration_metrics": migration_metrics,
        "migrations": migrations,
        "diff_patch": str(run_dir / "diff.patch"),
        "llm_calls": llm_call_summary(),
        "environment": env_snapshot(),
        "duration_seconds": round(time.perf_counter() - started, 3),
    }
    write_json(run_dir / "migration_only_report.json", output)
    print_json(output)
    return 0 if migration_metrics["migration_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
