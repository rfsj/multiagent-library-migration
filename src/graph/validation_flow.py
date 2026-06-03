from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Literal, Protocol

from src.graph.state import GraphState, require_current_step

MAX_STEP_RETRIES = 3
MAX_REPLAN_ATTEMPTS = 2


class ValidationRunner(Protocol):
    def validate_step(
        self,
        project_dir: Path,
        step: dict[str, Any],
        before_dir: Path,
        logs_dir: Path,
    ) -> dict[str, Any]:
        ...

    def evaluate_step(
        self,
        planned_step: dict[str, Any],
        migration_result: dict[str, Any],
        before_snapshot: dict[str, Any],
        validation_evidence: dict[str, Any],
        logs_dir: Path,
    ) -> dict[str, Any]:
        ...


def build_validation_node(validation_agent: ValidationRunner, logs_dir: Path):
    def validate_step(graph_state: GraphState) -> dict[str, Any]:
        step = require_current_step(graph_state)
        snapshot_dir = graph_state["current_snapshot_dir"]
        migration = graph_state["current_migration"]
        if snapshot_dir is None:
            raise RuntimeError(f"Missing snapshot for migration step {step['step_id']}")
        if migration is None:
            raise RuntimeError(f"Missing migration result for step {step['step_id']}")

        validation = validation_agent.validate_step(
            graph_state["project_dir"],
            step,
            snapshot_dir,
            logs_dir,
        )
        verdict = validation_agent.evaluate_step(
            planned_step=step,
            migration_result=migration,
            before_snapshot=_build_before_snapshot(snapshot_dir, step),
            validation_evidence=validation,
            logs_dir=logs_dir,
        )

        updates: dict[str, Any] = {
            "validations": [*graph_state["validations"], validation],
            "verdicts": [*graph_state["verdicts"], verdict],
            "current_validation": validation,
            "next_action": "next",
        }

        step_id = step["step_id"]
        if verdict["verdict"] == "accepted":
            updates["current_step_index"] = graph_state["current_step_index"] + 1
            updates["replan_feedback"] = None
            return updates

        if verdict["verdict"] == "rejected_plan":
            if (
                graph_state["replan_count"] >= MAX_REPLAN_ATTEMPTS
                or verdict["retry_recommendation"] == "stop"
            ):
                updates["abort_reason"] = (
                    f"Step {step_id} exceeded replanning limit after "
                    f"{graph_state['replan_count']} replans: {verdict['feedback_for_agent']}"
                )
                updates["stop_reason"] = f"rejected_plan:{step_id}"
                updates["next_action"] = "stop"
                return updates

            _restore_project_dir(snapshot_dir, graph_state["project_dir"])
            replan_count = graph_state["replan_count"] + 1
            replan_feedback = {
                "step_id": step_id,
                "rationale": verdict["rationale"],
                "feedback_for_agent": verdict["feedback_for_agent"],
            }
            updates.update({
                "diagnosis": None,
                "current_step": None,
                "current_step_index": 0,
                "current_snapshot_dir": None,
                "current_migration": None,
                "current_validation": None,
                "replan_count": replan_count,
                "replan_feedback": replan_feedback,
                "replan_history": [
                    *graph_state["replan_history"],
                    {
                        "step_id": step_id,
                        "replan_attempt": replan_count,
                        "feedback": replan_feedback,
                    },
                ],
                "next_action": "replan",
            })
            return updates

        retry_counts = dict(graph_state["retry_counts"])
        retry_counts[step_id] = retry_counts.get(step_id, 0) + 1
        if retry_counts[step_id] >= MAX_STEP_RETRIES or verdict["retry_recommendation"] == "stop":
            _restore_project_dir(snapshot_dir, graph_state["project_dir"])
            marked_files = _mark_manual_review_required(
                graph_state["project_dir"],
                step,
                verdict,
                retry_counts[step_id],
            )
            updates["retry_counts"] = retry_counts
            updates["failed_steps"] = [
                *graph_state["failed_steps"],
                {
                    "step_id": step_id,
                    "file": step.get("file"),
                    "manual_review_files": marked_files,
                    "verdict": verdict["verdict"],
                    "attempts": retry_counts[step_id],
                    "rationale": verdict["rationale"],
                    "feedback_for_agent": verdict["feedback_for_agent"],
                },
            ]
            updates["current_step_index"] = graph_state["current_step_index"] + 1
            updates["current_step"] = None
            updates["current_snapshot_dir"] = None
            updates["current_migration"] = None
            updates["current_validation"] = None
            updates["stop_reason"] = f"step_failed:{step_id}"
            updates["next_action"] = "next"
            return updates

        retry_step = dict(step)
        retry_step["retry_feedback"] = verdict["feedback_for_agent"]
        _restore_project_dir(snapshot_dir, graph_state["project_dir"])
        updates.update({
            "retry_counts": retry_counts,
            "current_step": retry_step,
            "current_snapshot_dir": None,
            "current_migration": None,
            "current_validation": None,
            "next_action": "retry",
        })
        return updates

    return validate_step


def route_after_validation(
    graph_state: GraphState,
) -> Literal["diagnose", "select_next_step", "snapshot_before_step", "__end__"]:
    if graph_state["next_action"] == "replan":
        return "diagnose"
    if graph_state["next_action"] == "retry":
        return "snapshot_before_step"
    if graph_state["next_action"] == "stop":
        return "__end__"
    return "select_next_step"


def _build_before_snapshot(before_dir: Path, step: dict[str, Any]) -> dict[str, Any]:
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


def _mark_manual_review_required(
    project_dir: Path,
    step: dict[str, Any],
    verdict: dict[str, Any],
    attempts: int,
) -> list[str]:
    marked_files = []
    for rel_path in _manual_review_targets(step):
        path = project_dir / rel_path
        if not path.exists() or not path.is_file():
            continue
        marker = _manual_review_marker(step, verdict, attempts, rel_path)
        content = path.read_text(encoding="utf-8")
        if marker in content:
            continue
        updated = _insert_manual_review_marker(content, marker, step)
        path.write_text(updated, encoding="utf-8")
        marked_files.append(rel_path)
    return marked_files


def _manual_review_targets(step: dict[str, Any]) -> list[str]:
    targets = []
    file = step.get("file")
    if file:
        targets.append(str(file))
    for rel_path in step.get("allowed_files", []):
        rel_path = str(rel_path)
        if rel_path not in targets and rel_path.endswith(".py"):
            targets.append(rel_path)
    return targets


def _manual_review_marker(
    step: dict[str, Any],
    verdict: dict[str, Any],
    attempts: int,
    rel_path: str,
) -> str:
    step_id = step["step_id"]
    feedback = str(verdict.get("feedback_for_agent", "")).replace("\n", " ")
    rationale = str(verdict.get("rationale", "")).replace("\n", " ")
    description = str(step.get("description", "")).replace("\n", " ")
    return (
        f"# &&&&&&&&&&&&& MIGRATION MANUAL REVIEW START {step_id}\n"
        f"# File: {rel_path}\n"
        f"# Planned change: {description}\n"
        f"# Automatic migration failed after {attempts} attempts.\n"
        "# Keep the original behavior and migrate this file manually before approval.\n"
        f"# Validation rationale: {rationale}\n"
        f"# Validation feedback: {feedback}\n"
        f"# &&&&&&&&&&&&& MIGRATION MANUAL REVIEW END {step_id}\n"
    )


def _insert_manual_review_marker(content: str, marker: str, step: dict[str, Any]) -> str:
    symbols = step.get("allowed_symbols", [])
    if symbols:
        symbol = symbols[0]
        lines = content.splitlines(keepends=True)
        for index, line in enumerate(lines):
            if line.startswith(f"def {symbol}(") or line.startswith(f"async def {symbol}("):
                lines.insert(index, marker + "\n")
                return "".join(lines)
    suffix = "" if content.endswith("\n") else "\n"
    return content + suffix + "\n" + marker
