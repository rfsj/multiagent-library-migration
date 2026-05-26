from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
    abort_reason: str | None = None
    replan_count: int = 0
    replan_feedback: dict[str, Any] | None = None
    replan_history: list[dict[str, Any]] = field(default_factory=list)
