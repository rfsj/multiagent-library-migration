from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class WorkflowState:
    task_id: str
    project_dir: Path
    run_dir: Path
    source_library: str = "pandas"
    target_library: str = "polars"
    diagnosis: dict[str, Any] | None = None
    migrations: list[dict[str, Any]] = field(default_factory=list)
    validations: list[dict[str, Any]] = field(default_factory=list)
