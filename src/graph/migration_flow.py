from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Literal, Protocol

from src.graph.state import GraphState, require_current_step


class MigrationRunner(Protocol):
    def run_step(self, project_dir: Path, step: dict[str, Any], logs_dir: Path) -> dict[str, Any]:
        ...


def select_next_step(graph_state: GraphState) -> dict[str, Any]:
    diagnosis = graph_state["diagnosis"] or {}
    migration_steps = diagnosis.get("migration_steps", [])
    step_index = graph_state["current_step_index"]
    if step_index >= len(migration_steps):
        return {
            "current_step": None,
            "current_snapshot_dir": None,
            "stop_reason": "completed",
            "next_action": "stop",
        }

    step = dict(migration_steps[step_index])
    step["source_library"] = diagnosis.get("source_library")
    step["target_library"] = diagnosis.get("target_library")
    retry_feedback = graph_state["retry_counts"].get(step["step_id"])
    if retry_feedback and graph_state["current_step"] and graph_state["current_step"].get("retry_feedback"):
        step["retry_feedback"] = graph_state["current_step"]["retry_feedback"]
    return {
        "current_step": step,
        "current_snapshot_dir": None,
        "current_migration": None,
        "current_validation": None,
        "stop_reason": None,
        "next_action": None,
    }


def build_snapshot_node(snapshots_dir: Path):
    def snapshot_before_step(graph_state: GraphState) -> dict[str, Any]:
        step = require_current_step(graph_state)
        before_step = snapshots_dir / f"before_{step['step_id']}"
        if before_step.exists():
            shutil.rmtree(before_step)
        shutil.copytree(
            graph_state["project_dir"],
            before_step,
            ignore=shutil.ignore_patterns(".venv", "__pycache__"),
        )
        return {"current_snapshot_dir": before_step}

    return snapshot_before_step


def build_migration_node(migration_agent: MigrationRunner, logs_dir: Path):
    def migrate_step(graph_state: GraphState) -> dict[str, Any]:
        step = require_current_step(graph_state)
        migration = migration_agent.run_step(graph_state["project_dir"], step, logs_dir)
        return {
            "migrations": [*graph_state["migrations"], migration],
            "current_migration": migration,
        }

    return migrate_step


def route_after_selection(graph_state: GraphState) -> Literal["snapshot_before_step", "__end__"]:
    if graph_state["current_step"] is None:
        return "__end__"
    return "snapshot_before_step"
