from __future__ import annotations

import shutil
from pathlib import Path

from src.agents.diagnosis_agent import DiagnosisAgent
from src.agents.migration_agent import MigrationAgent
from src.agents.validation_agent import ValidationAgent
from src.graph.state import WorkflowState


def run_simple_workflow(state: WorkflowState) -> WorkflowState:
    logs_dir = state.run_dir / "logs"
    snapshots_dir = state.run_dir / "snapshots"
    diagnosis_agent = DiagnosisAgent()
    migration_agent = MigrationAgent()
    validation_agent = ValidationAgent()

    state.diagnosis = diagnosis_agent.run(
        state.project_dir,
        logs_dir,
        source_library=state.source_library,
        target_library=state.target_library,
    )
    for step in state.diagnosis["migration_steps"]:
        before_step = snapshots_dir / f"before_{step['step_id']}"
        if before_step.exists():
            shutil.rmtree(before_step)
        shutil.copytree(state.project_dir, before_step, ignore=shutil.ignore_patterns(".venv", "__pycache__"))
        migration = migration_agent.run_step(state.project_dir, step, logs_dir)
        validation = validation_agent.validate_step(state.project_dir, step, before_step, logs_dir)
        state.migrations.append(migration)
        state.validations.append(validation)
        if validation["status"] != "approved":
            break
    return state
