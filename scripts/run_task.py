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

from src.agents.validation_agent import ValidationAgent
from src.evaluation.metrics import build_metrics
from src.evaluation.report_generator import environment_versions, git_commit, write_report
from src.graph.state import WorkflowState
from src.graph.workflow import run_simple_workflow
from src.tools.diff_analyzer import unified_diff
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

    shutil.copytree(task_dir / "input_project", project_dir)
    shutil.copytree(project_dir, before_dir)
    _copy_prompts(run_dir)

    if not args.skip_install:
        _install_project_dependencies(project_dir, logs_dir / "install_before.log")

    tests_before = run_pytest(project_dir, logs_dir / "tests_before.log")
    state = WorkflowState(
        task_id=args.task_id,
        project_dir=project_dir,
        run_dir=run_dir,
        source_library=metadata["source_library"],
        target_library=metadata["target_library"],
    )
    state = run_simple_workflow(state)

    if state.abort_reason:
        shutil.rmtree(project_dir)
        shutil.copytree(before_dir, project_dir)

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
        final_validation = ValidationAgent().final_validate(project_dir, before_dir, logs_dir, state.source_library)
    diff_text = unified_diff(before_dir, project_dir)
    (run_dir / "diff.patch").write_text(diff_text, encoding="utf-8")

    metrics = build_metrics(tests_before, tests_after, final_validation, state.retry_counts)
    report = {
        "task_id": metadata["task_id"],
        "source_library": metadata["source_library"],
        "target_library": metadata["target_library"],
        **metrics,
        "verdicts": state.verdicts,
        "retry_counts": state.retry_counts,
        "abort_reason": state.abort_reason,
        "replan_count": state.replan_count,
        "replan_history": state.replan_history,
        "verdict_summary": [
            {"step_id": verdict["step_id"], "verdict": verdict["verdict"]}
            for verdict in state.verdicts
        ],
        "environment": environment_versions(),
        "git_commit": git_commit(),
        "run_dir": str(run_dir),
        "logs_dir": str(logs_dir),
    }
    write_report(run_dir / "report.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "success" else 1


def _install_project_dependencies(project_dir: Path, log_file: Path) -> None:
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


if __name__ == "__main__":
    raise SystemExit(main())
