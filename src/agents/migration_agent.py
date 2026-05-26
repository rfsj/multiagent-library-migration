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
        target = project_dir / rel_file
        original = target.read_text(encoding="utf-8")
        retry_feedback = step.get("retry_feedback")
        migrated = self._migrate_python_source(original)
        changed = migrated != original
        if changed:
            target.write_text(migrated, encoding="utf-8")
            self._ensure_polars_dependency(project_dir)

        result = {
            "agent": self.name,
            "step_id": step["step_id"],
            "file": step["file"],
            "changed": changed,
            "status": "completed" if changed else "no_change",
            "retry_feedback_received": bool(retry_feedback),
        }
        (logs_dir / f"{step['step_id']}_migration.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        return result

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

    def _ensure_polars_dependency(self, project_dir: Path) -> None:
        requirements = project_dir / "requirements.txt"
        if requirements.exists():
            content = requirements.read_text(encoding="utf-8")
            if not re.search(r"^polars([<>=!~]=|==|$)", content, flags=re.MULTILINE):
                suffix = "" if content.endswith("\n") else "\n"
                requirements.write_text(content + suffix + "polars==1.17.1\n", encoding="utf-8")
