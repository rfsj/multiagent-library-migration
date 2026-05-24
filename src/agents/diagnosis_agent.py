from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.tools.project_scanner import scan_project


class DiagnosisAgent:
    """Read-only agent that identifies migration scope."""

    name = "diagnosis_agent"

    def run(
        self,
        project_dir: Path,
        logs_dir: Path,
        source_library: str,
        target_library: str,
    ) -> dict[str, Any]:
        logs_dir.mkdir(parents=True, exist_ok=True)
        scan = scan_project(project_dir, source_library)
        plan = {
            "agent": self.name,
            "source_library": source_library,
            "target_library": target_library,
            "read_only": True,
            "dependency_files": scan["dependency_files"],
            "affected_files": scan["affected_files"],
            "related_tests": scan["test_files"],
            "complexity": self._classify(scan),
            "migration_steps": self._build_steps(scan, source_library, target_library),
        }
        (logs_dir / "diagnosis_plan.json").write_text(
            json.dumps(plan, indent=2), encoding="utf-8"
        )
        return plan

    def _classify(self, scan: dict[str, Any]) -> dict[str, str]:
        complexity: dict[str, str] = {}
        for file_path in scan["affected_files"]:
            calls = [c for c in scan["source_api_calls"] if c["file"] == file_path]
            supported = {c["api"] for c in calls}
            if supported <= {"pd.read_csv", "boolean_filter", "column_selection", "sort_values"}:
                complexity[file_path] = "low"
            else:
                complexity[file_path] = "medium"
        return complexity

    def _build_steps(
        self,
        scan: dict[str, Any],
        source_library: str,
        target_library: str,
    ) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []
        for index, file_path in enumerate(scan["affected_files"], start=1):
            steps.append(
                {
                    "step_id": f"step_{index:03d}",
                    "file": file_path,
                    "description": f"Migrate {source_library} usage to {target_library}.",
                    "allowed_files": [file_path],
                    "status": "planned",
                }
            )
        return steps
