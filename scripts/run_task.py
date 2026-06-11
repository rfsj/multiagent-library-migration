from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import llm_proxy
from src.agents.implementation_review_agent import ImplementationReviewAgent
from src.agents.validation_agent import ValidationAgent
from src.evaluation.metrics import build_metrics
from src.evaluation.report_generator import environment_versions, git_commit, write_report
from src.evaluation.semantic_probe import run_semantic_probe
from src.migration_config import MigrationConfig
from src.graph.state import WorkflowState
from src.graph.workflow import run_simple_workflow
from src.tools.diff_analyzer import unified_diff
from src.tools.project_scanner import build_project_audit
from src.tools.test_runner import run_pytest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("--skip-install", action="store_true")
    args = parser.parse_args()

    task_dir = ROOT / "benchmark" / args.task_id
    metadata = json.loads((task_dir / "metadata.json").read_text(encoding="utf-8"))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = ROOT / "experiments" / "runs" / f"{args.task_id}_{timestamp}"
    project_dir = run_dir / "project"
    before_dir = run_dir / "snapshots" / "before_migration"
    logs_dir = run_dir / "logs"
    run_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    llm_proxy.configure(logs_dir / "llm_proxy.jsonl")

    shutil.copytree(task_dir / "input_project", project_dir)
    shutil.copytree(project_dir, before_dir)
    _copy_prompts(run_dir)
    project_audit = build_project_audit(
        project_dir,
        metadata["source_library"],
        metadata["target_library"],
    )
    (logs_dir / "project_audit.json").write_text(
        json.dumps(project_audit, indent=2),
        encoding="utf-8",
    )

    if not args.skip_install:
        _install_project_dependencies(project_dir, logs_dir / "install_before.log")

    tests_before = run_pytest(project_dir, logs_dir / "tests_before.log")
    if not tests_before["passed"]:
        final_validation = {
            "agent": "validation_agent",
            "tests": "skipped",
            "old_imports_remaining": None,
            "unmigrated_uses": None,
            "out_of_scope_changes": None,
            "status": "baseline_failed",
            "skipped": True,
            "reason": "Baseline tests failed before migration; migration was not executed.",
        }
        tests_after = {
            "status": "skipped",
            "passed": False,
            "returncode": None,
            "log_file": None,
        }
        (logs_dir / "final_validation.json").write_text(
            json.dumps(final_validation, indent=2),
            encoding="utf-8",
        )
        (run_dir / "diff.patch").write_text(unified_diff(before_dir, project_dir), encoding="utf-8")
        report = _build_report(
            metadata=metadata,
            tests_before=tests_before,
            tests_after=tests_after,
            final_validation=final_validation,
            run_dir=run_dir,
            logs_dir=logs_dir,
            project_audit=project_audit,
            verdicts=[],
            retry_counts={},
            failed_steps=[],
            abort_reason=final_validation["reason"],
            replan_count=0,
            replan_history=[],
        )
        write_report(run_dir / "report.json", report)
        print(json.dumps(report, indent=2))
        return 1

    state = WorkflowState(
        task_id=args.task_id,
        project_dir=project_dir,
        run_dir=run_dir,
        source_library=metadata["source_library"],
        target_library=metadata["target_library"],
    )
    state = run_simple_workflow(state)

    if not args.skip_install:
        _install_project_dependencies(project_dir, logs_dir / "install_after.log")

    tests_after = run_pytest(project_dir, logs_dir / "tests_after.log")
    if state.abort_reason:
        final_validation = {
            "agent": "validation_agent",
            "tests": "skipped",
            "old_imports_remaining": None,
            "unmigrated_uses": None,
            "out_of_scope_changes": None,
            "status": "aborted",
            "skipped": True,
            "reason": state.abort_reason,
        }
    else:
        final_validation = ValidationAgent().final_validate(
            project_dir,
            before_dir,
            logs_dir,
            state.source_library,
            allowed_files=_allowed_files_from_diagnosis(state.diagnosis),
        )
    diff_text = unified_diff(before_dir, project_dir)
    (run_dir / "diff.patch").write_text(diff_text, encoding="utf-8")

    semantic_risks: list[dict] = []
    if not state.abort_reason and final_validation.get("status") == "approved":
        llm_proxy.set_label("semantic_probe")
        semantic_risks = run_semantic_probe(
            review_agent=ImplementationReviewAgent(),
            diagnosis=state.diagnosis,
            before_dir=before_dir,
            project_dir=project_dir,
            accepted_step_ids=_accepted_step_ids(state.verdicts),
            logs_dir=logs_dir,
        )

    report = _build_report(
        metadata=metadata,
        tests_before=tests_before,
        tests_after=tests_after,
        final_validation=final_validation,
        run_dir=run_dir,
        logs_dir=logs_dir,
        project_audit=project_audit,
        verdicts=state.verdicts,
        retry_counts=state.retry_counts,
        failed_steps=state.failed_steps,
        abort_reason=state.abort_reason,
        replan_count=state.replan_count,
        replan_history=state.replan_history,
        diagnosis=state.diagnosis,
        semantic_risks=semantic_risks,
    )
    write_report(run_dir / "report.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "success" else 1


def _install_project_dependencies(project_dir: Path, log_file: Path) -> None:
    requirements = project_dir / "requirements.txt"
    if not requirements.exists():
        log_file.write_text("No requirements.txt found; dependency installation skipped.\n", encoding="utf-8")
        return
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
        cwd=project_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    log_file.write_text(proc.stdout, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"Dependency installation failed. See {log_file}")


def _copy_prompts(run_dir: Path) -> None:
    source = ROOT / "prompts"
    target = run_dir / "prompts"
    target.mkdir(parents=True, exist_ok=True)
    for prompt in source.glob("*.md"):
        shutil.copy2(prompt, target / prompt.name)


def _allowed_files_from_diagnosis(diagnosis: dict | None) -> list[str] | None:
    if not diagnosis:
        return None
    allowed = set()
    for step in diagnosis.get("migration_steps", []):
        allowed.update(step.get("allowed_files", []))
    return sorted(allowed)


def _accepted_step_ids(verdicts: list[dict]) -> list[str]:
    latest_by_step: dict[str, str] = {}
    for verdict in verdicts:
        latest_by_step[verdict["step_id"]] = verdict["verdict"]
    return [step_id for step_id, v in latest_by_step.items() if v == "accepted"]


def _build_report(
    *,
    metadata: dict,
    tests_before: dict,
    tests_after: dict,
    final_validation: dict,
    run_dir: Path,
    logs_dir: Path,
    project_audit: dict,
    verdicts: list[dict],
    retry_counts: dict[str, int],
    failed_steps: list[dict],
    abort_reason: str | None,
    replan_count: int,
    replan_history: list[dict],
    diagnosis: dict | None = None,
    semantic_risks: list[dict] | None = None,
) -> dict:
    semantic_risks = semantic_risks or []
    metrics = build_metrics(
        tests_before, tests_after, final_validation, retry_counts, semantic_risks, verdicts
    )
    return {
        "task_id": metadata["task_id"],
        "source_library": metadata["source_library"],
        "target_library": metadata["target_library"],
        **metrics,
        "semantic_risks": semantic_risks,
        "migration_config": MigrationConfig.from_env().as_dict(),
        "llm_calls": {
            "total": llm_proxy.total_calls(),
            "by_label": llm_proxy.call_counts(),
        },
        "project_audit": {
            "migration_needed": project_audit["migration_needed"],
            "affected_source_files": project_audit["affected_source_files"],
            "test_files_with_source_library_usage": project_audit[
                "test_files_with_source_library_usage"
            ],
            "dependency_summary": project_audit["dependency_summary"],
        },
        "verdicts": verdicts,
        "retry_counts": retry_counts,
        "failed_steps": failed_steps,
        "migration_step_summary": _migration_step_summary(diagnosis, verdicts, failed_steps),
        "abort_reason": abort_reason,
        "replan_count": replan_count,
        "replan_history": replan_history,
        "verdict_summary": [
            {"step_id": verdict["step_id"], "verdict": verdict["verdict"]}
            for verdict in verdicts
        ],
        "environment": environment_versions(),
        "git_commit": git_commit(),
        "run_dir": str(run_dir),
        "logs_dir": str(logs_dir),
    }


def _migration_step_summary(
    diagnosis: dict | None,
    verdicts: list[dict],
    failed_steps: list[dict],
) -> dict:
    planned_steps = diagnosis.get("migration_steps", []) if diagnosis else []
    latest_by_step: dict[str, str] = {}
    for verdict in verdicts:
        latest_by_step[verdict["step_id"]] = verdict["verdict"]

    accepted = sorted(
        step_id for step_id, verdict in latest_by_step.items() if verdict == "accepted"
    )
    failed = [step["step_id"] for step in failed_steps]
    attempted = sorted(latest_by_step)
    planned = [step["step_id"] for step in planned_steps]
    not_attempted = [step_id for step_id in planned if step_id not in latest_by_step]
    return {
        "planned_steps": len(planned),
        "attempted_steps": len(attempted),
        "accepted_steps": len(accepted),
        "failed_steps": len(failed),
        "not_attempted_steps": len(not_attempted),
        "accepted_step_ids": accepted,
        "failed_step_ids": failed,
        "not_attempted_step_ids": not_attempted,
    }


if __name__ == "__main__":
    raise SystemExit(main())
