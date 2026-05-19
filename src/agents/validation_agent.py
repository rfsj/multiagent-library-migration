from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from src.tools.diff_analyzer import analyze_diff, changed_files
from src.tools.project_scanner import scan_project
from src.tools.test_runner import run_pytest


class ValidationAgent:
    """Independent validation agent for step and final checks."""

    name = "validation_agent"

    def validate_step(
        self,
        project_dir: Path,
        step: dict[str, Any],
        before_dir: Path,
        logs_dir: Path,
    ) -> dict[str, Any]:
        logs_dir.mkdir(parents=True, exist_ok=True)
        changed = changed_files(before_dir, project_dir)
        allowed = set(step.get("allowed_files", [])) | {"requirements.txt"}
        out_of_scope = [path for path in changed if path not in allowed]
        self._install_dependencies(project_dir, logs_dir / f"{step['step_id']}_install.log")
        tests = run_pytest(project_dir, logs_dir / f"{step['step_id']}_pytest.log")
        result = {
            "agent": self.name,
            "step_id": step["step_id"],
            "changed_files": changed,
            "out_of_scope_changes": out_of_scope,
            "tests": tests["status"],
            "status": "approved" if not out_of_scope and tests["passed"] else "rejected",
        }
        (logs_dir / f"{step['step_id']}_validation.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        return result

    def final_validate(self, project_dir: Path, before_dir: Path, logs_dir: Path) -> dict[str, Any]:
        scan = scan_project(project_dir)
        diff = analyze_diff(before_dir, project_dir)
        tests = run_pytest(project_dir, logs_dir / "final_pytest.log")
        result = {
            "agent": self.name,
            "tests": tests["status"],
            "old_imports_remaining": len(scan["pandas_imports"]),
            "unmigrated_uses": len(scan["pandas_api_calls"]),
            "out_of_scope_changes": diff["out_of_scope_changes"],
            "status": "approved"
            if tests["passed"]
            and len(scan["pandas_imports"]) == 0
            and len(scan["pandas_api_calls"]) == 0
            and diff["out_of_scope_changes"] == 0
            else "rejected",
        }
        (logs_dir / "final_validation.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        return result

    def _install_dependencies(self, project_dir: Path, log_file: Path) -> None:
        requirements = project_dir / "requirements.txt"
        if not requirements.exists():
            return
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
            cwd=project_dir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        log_file.write_text(proc.stdout, encoding="utf-8")
