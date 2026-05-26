from __future__ import annotations

import shutil
from pathlib import Path

from src.agents.diagnosis_agent import DiagnosisAgent
from src.agents.migration_agent import MigrationAgent
from src.agents.validation_agent import ValidationAgent
from src.graph.state import WorkflowState

MAX_STEP_RETRIES = 3
MAX_REPLAN_ATTEMPTS = 2


def run_simple_workflow(state: WorkflowState) -> WorkflowState:
    logs_dir = state.run_dir / "logs"
    snapshots_dir = state.run_dir / "snapshots"
    diagnosis_agent = DiagnosisAgent()
    migration_agent = MigrationAgent()
    validation_agent = ValidationAgent()

    while True:
        state.diagnosis = diagnosis_agent.run(
            state.project_dir,
            logs_dir,
            source_library=state.source_library,
            target_library=state.target_library,
            replan_feedback=state.replan_feedback,
            replan_attempt=state.replan_count,
        )

        replan_requested = False
        for step in state.diagnosis["migration_steps"]:
            step_id = step["step_id"]
            state.retry_counts.setdefault(step_id, 0)

            while True:
                before_step = snapshots_dir / f"before_{step_id}"
                if before_step.exists():
                    shutil.rmtree(before_step)
                shutil.copytree(state.project_dir, before_step, ignore=shutil.ignore_patterns(".venv", "__pycache__"))

                migration = migration_agent.run_step(state.project_dir, step, logs_dir)
                validation = validation_agent.validate_step(state.project_dir, step, before_step, logs_dir)
                verdict = validation_agent.evaluate_step(
                    planned_step=step,
                    migration_result=migration,
                    before_snapshot=_build_before_snapshot(before_step, step),
                    validation_evidence=validation,
                    logs_dir=logs_dir,
                )

                state.migrations.append(migration)
                state.validations.append(validation)
                state.verdicts.append(verdict)

                if verdict["verdict"] == "accepted":
                    state.replan_feedback = None
                    break

                if verdict["verdict"] == "rejected_plan":
                    if state.replan_count >= MAX_REPLAN_ATTEMPTS or verdict["retry_recommendation"] == "stop":
                        state.abort_reason = (
                            f"Step {step_id} exceeded replanning limit after {state.replan_count} replans: "
                            f"{verdict['feedback_for_agent']}"
                        )
                        return state
                    _restore_project_dir(before_step, state.project_dir)
                    state.replan_count += 1
                    state.replan_feedback = {
                        "step_id": step_id,
                        "rationale": verdict["rationale"],
                        "feedback_for_agent": verdict["feedback_for_agent"],
                    }
                    state.replan_history.append({
                        "step_id": step_id,
                        "replan_attempt": state.replan_count,
                        "feedback": state.replan_feedback,
                    })
                    replan_requested = True
                    break

                state.retry_counts[step_id] += 1
                if state.retry_counts[step_id] >= MAX_STEP_RETRIES or verdict["retry_recommendation"] == "stop":
                    state.abort_reason = (
                        f"Step {step_id} stopped after {state.retry_counts[step_id]} attempts: "
                        f"{verdict['rationale']}"
                    )
                    return state

                step["retry_feedback"] = verdict["feedback_for_agent"]

            if replan_requested:
                break

        if not replan_requested:
            return state


def _build_before_snapshot(before_dir: Path, step: dict[str, object]) -> dict[str, object]:
    files: dict[str, str] = {}
    for rel_path in step.get("allowed_files", []):
        path = before_dir / rel_path
        if path.exists():
            files[str(rel_path)] = path.read_text(encoding="utf-8")
    return {
        "before_dir": str(before_dir),
        "files": files,
    }


def _restore_project_dir(source_dir: Path, target_dir: Path) -> None:
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(source_dir, target_dir, ignore=shutil.ignore_patterns(".venv", "__pycache__"))
