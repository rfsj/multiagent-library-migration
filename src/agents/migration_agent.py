from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class MigrationAgent:
    """Executes one planned migration step at a time."""

    name = "migration_agent"

    def run_step(self, project_dir: Path, step: dict[str, Any], logs_dir: Path) -> dict[str, Any]:
        logs_dir.mkdir(parents=True, exist_ok=True)
        rel_file = Path(step["file"])
        self._validate_step_scope(step, rel_file)
        target = project_dir / rel_file
        original = target.read_text(encoding="utf-8")
        retry_feedback = step.get("retry_feedback")
        migrated = self._migrate_file(rel_file, original, step)
        changed_files: list[str] = []
        if migrated != original:
            target.write_text(migrated, encoding="utf-8")
            changed_files.append(str(rel_file))

        if rel_file.name != "requirements.txt" and "requirements.txt" in step.get("allowed_files", []):
            if self._migrate_allowed_requirements(project_dir, step):
                changed_files.append("requirements.txt")

        result = {
            "agent": self.name,
            "step_id": step["step_id"],
            "file": step["file"],
            "changed": bool(changed_files),
            "changed_files": changed_files,
            "status": "completed" if changed_files else "no_change",
            "retry_feedback_received": bool(retry_feedback),
        }
        (logs_dir / f"{step['step_id']}_migration.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        return result

    def _validate_step_scope(self, step: dict[str, Any], rel_file: Path) -> None:
        allowed_files = set(step.get("allowed_files", []))
        if str(rel_file) not in allowed_files:
            raise ValueError(
                f"Step {step['step_id']} targets {rel_file}, "
                "but that file is not listed in allowed_files."
            )

    def _migrate_file(self, rel_file: Path, source: str, step: dict[str, Any]) -> str:
        if rel_file.name == "requirements.txt":
            return self._migrate_requirements(source, step)
        if rel_file.suffix == ".py":
            return self._migrate_python_source(source)
        return source

    def _migrate_allowed_requirements(self, project_dir: Path, step: dict[str, Any]) -> bool:
        requirements = project_dir / "requirements.txt"
        if not requirements.exists():
            return False
        original = requirements.read_text(encoding="utf-8")
        migrated = self._migrate_requirements(original, step)
        if migrated == original:
            return False
        requirements.write_text(migrated, encoding="utf-8")
        return True

    def _migrate_python_source(self, source: str) -> str:
        output = source
        output = re.sub(r"^import pandas as pd$", "import polars as pl", output, flags=re.MULTILINE)
        output = output.replace("pd.read_csv(", "pl.read_csv(")
        output = re.sub(
            r'(\w+)\s*=\s*\1\[\1\["([^"]+)"\]\s*==\s*"([^"]+)"\]',
            r'\1 = \1.filter(pl.col("\2") == "\3")',
            output,
        )
        output = re.sub(
            r"(\w+)\s*=\s*\1\[\1\['([^']+)'\]\s*==\s*'([^']+)'\]",
            r"\1 = \1.filter(pl.col('\2') == '\3')",
            output,
        )
        output = re.sub(
            r'(\w+)\[\[([^\]]+)\]\]\.sort_values\(([^)]+)\)',
            r"\1.select([\2]).sort(\3)",
            output,
        )
        output = re.sub(r"\.sort_values\(", ".sort(", output)
        return output

    def _migrate_requirements(self, source: str, step: dict[str, Any]) -> str:
        target_library = step.get("target_library")
        if not target_library:
            raise ValueError(
                f"Step {step['step_id']} targets requirements.txt, "
                "but no target_library was provided by diagnosis."
            )
        if re.search(rf"^{re.escape(target_library)}([<>=!~]=|==|$)", source, flags=re.MULTILINE):
            return source
        suffix = "" if source.endswith("\n") else "\n"
        return source + suffix + f"{target_library}\n"
