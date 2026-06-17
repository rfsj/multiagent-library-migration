from __future__ import annotations

import argparse
import time

from experiment_utils import (
    configure_llm_logging,
    create_eval_run_dir,
    env_snapshot,
    llm_call_summary,
    load_task_metadata,
    print_json,
    setup_project_copy,
    write_json,
)
from src import llm_proxy
from src.evaluation.plan_validator import validate_plan
from src.graph.workflow import _build_diagnosis_agent
from src.tools.project_scanner import build_project_audit


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run only the Planner/Diagnosis agent for one benchmark task."
    )
    parser.add_argument("task_id")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    started = time.perf_counter()
    metadata = load_task_metadata(args.task_id)
    run_dir = create_eval_run_dir(args.task_id, "planner", args.run_id)
    logs_dir = run_dir / "logs"
    project_dir, before_dir = setup_project_copy(args.task_id, run_dir)
    configure_llm_logging(logs_dir)

    audit = build_project_audit(
        project_dir,
        metadata["source_library"],
        metadata["target_library"],
    )
    write_json(logs_dir / "project_audit_eval.json", audit)

    agent = _build_diagnosis_agent()
    llm_proxy.set_label("planner_only")
    diagnosis = agent.run(
        project_dir,
        logs_dir,
        metadata["source_library"],
        metadata["target_library"],
    )
    plan_validation = validate_plan(
        diagnosis,
        audit,
        metadata.get("validity_oracle") or metadata.get("expected_planner"),
    )
    write_json(logs_dir / "plan_validation.json", plan_validation)

    output = {
        "phase": "planner_only",
        "status": "success",
        "task_id": args.task_id,
        "source_library": metadata["source_library"],
        "target_library": metadata["target_library"],
        "run_dir": str(run_dir),
        "project_dir": str(project_dir),
        "before_dir": str(before_dir),
        "logs_dir": str(logs_dir),
        "diagnosis_plan": str(logs_dir / "diagnosis_plan.json"),
        "planner_version": diagnosis.get("planner_version"),
        "affected_source_files": diagnosis.get("affected_source_files", []),
        "migration_step_count": len(diagnosis.get("migration_steps", [])),
        "human_review_required": diagnosis.get("human_review_required", False),
        "human_review_reasons": diagnosis.get("human_review_reasons", []),
        "planner_warnings": diagnosis.get("planner_warnings", []),
        **plan_validation,
        "llm_calls": llm_call_summary(),
        "environment": env_snapshot(),
        "duration_seconds": round(time.perf_counter() - started, 3),
    }
    write_json(run_dir / "planner_only_report.json", output)
    print_json(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
