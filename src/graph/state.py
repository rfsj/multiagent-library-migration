from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, TypedDict


@dataclass
class WorkflowState:
    task_id: str
    project_dir: Path
    run_dir: Path
    source_library: str
    target_library: str
    diagnosis: dict[str, Any] | None = None
    migrations: list[dict[str, Any]] = field(default_factory=list)
    validations: list[dict[str, Any]] = field(default_factory=list)
    verdicts: list[dict[str, Any]] = field(default_factory=list)
    retry_counts: dict[str, int] = field(default_factory=dict)
    failed_steps: list[dict[str, Any]] = field(default_factory=list)
    abort_reason: str | None = None
    replan_count: int = 0
    replan_feedback: dict[str, Any] | None = None
    replan_history: list[dict[str, Any]] = field(default_factory=list)


class GraphState(TypedDict):
    task_id: str
    project_dir: Path
    run_dir: Path
    source_library: str
    target_library: str
    diagnosis: Optional[dict[str, Any]]
    migrations: list[dict[str, Any]]
    validations: list[dict[str, Any]]
    verdicts: list[dict[str, Any]]
    retry_counts: dict[str, int]
    failed_steps: list[dict[str, Any]]
    abort_reason: Optional[str]
    replan_count: int
    replan_feedback: Optional[dict[str, Any]]
    replan_history: list[dict[str, Any]]
    current_step_index: int
    current_step: Optional[dict[str, Any]]
    current_snapshot_dir: Optional[Path]
    current_migration: Optional[dict[str, Any]]
    current_validation: Optional[dict[str, Any]]
    stop_reason: Optional[str]
    next_action: Optional[str]


def require_current_step(graph_state: GraphState) -> dict[str, Any]:
    step = graph_state["current_step"]
    if step is None:
        raise RuntimeError(
            "Migration graph expected a current step but none was selected."
        )
    return step


def to_graph_state(state: WorkflowState) -> GraphState:
    return {
        "task_id": state.task_id,
        "project_dir": state.project_dir,
        "run_dir": state.run_dir,
        "source_library": state.source_library,
        "target_library": state.target_library,
        "diagnosis": state.diagnosis,
        "migrations": list(state.migrations),
        "validations": list(state.validations),
        "verdicts": list(state.verdicts),
        "retry_counts": dict(state.retry_counts),
        "failed_steps": list(state.failed_steps),
        "abort_reason": state.abort_reason,
        "replan_count": state.replan_count,
        "replan_feedback": state.replan_feedback,
        "replan_history": list(state.replan_history),
        "current_step_index": 0,
        "current_step": None,
        "current_snapshot_dir": None,
        "current_migration": None,
        "current_validation": None,
        "stop_reason": None,
        "next_action": None,
    }


def to_workflow_state(
    original: WorkflowState, graph_state: GraphState
) -> WorkflowState:
    original.diagnosis = graph_state["diagnosis"]
    original.migrations = graph_state["migrations"]
    original.validations = graph_state["validations"]
    original.verdicts = graph_state["verdicts"]
    original.retry_counts = graph_state["retry_counts"]
    original.failed_steps = graph_state["failed_steps"]
    original.abort_reason = graph_state["abort_reason"]
    original.replan_count = graph_state["replan_count"]
    original.replan_feedback = graph_state["replan_feedback"]
    original.replan_history = graph_state["replan_history"]
    return original
