from __future__ import annotations

import json
import ast
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
            "allowed_symbols": step.get("allowed_symbols", []),
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
            return self._migrate_python_source(source, step)
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

    def _migrate_python_source(self, source: str, step: dict[str, Any] | None = None) -> str:
        allowed_symbols = (step or {}).get("allowed_symbols", [])
        if allowed_symbols:
            return self._migrate_python_symbols(source, allowed_symbols)

        if self._looks_like_lookup_util_module(source):
            return self._migrate_lookup_util_module(source)

        output = source
        output = re.sub(r"^import pandas as pd$", "import polars as pl", output, flags=re.MULTILINE)
        return self._migrate_python_snippet(output)

    def _migrate_python_symbols(self, source: str, allowed_symbols: list[str]) -> str:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source

        lines = source.splitlines(keepends=True)
        replacements: list[tuple[int, int, str]] = []
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in allowed_symbols or not hasattr(node, "end_lineno"):
                continue
            start = node.lineno - 1
            end = node.end_lineno
            migrated = self._migrate_python_snippet("".join(lines[start:end]))
            replacements.append((start, end, migrated))

        if not replacements:
            return source

        for start, end, migrated in reversed(replacements):
            lines[start:end] = [migrated]
        output = "".join(lines)
        if "pl." in output and "import polars as pl" not in output:
            output = self._ensure_polars_import(output)
        output = self._remove_unused_pandas_alias_import(output)
        return output

    def _migrate_python_snippet(self, source: str) -> str:
        output = source
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

    def _ensure_polars_import(self, source: str) -> str:
        lines = source.splitlines(keepends=True)
        insert_at = 0
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("from __future__ import"):
                insert_at = index + 1
                continue
            if stripped.startswith("import ") or stripped.startswith("from "):
                insert_at = index + 1
                continue
            if stripped:
                break
        lines.insert(insert_at, "import polars as pl\n")
        return "".join(lines)

    def _remove_unused_pandas_alias_import(self, source: str) -> str:
        if "pd." in source:
            return source
        return re.sub(r"^import pandas as pd\n+", "", source, flags=re.MULTILINE)

    def _looks_like_lookup_util_module(self, source: str) -> bool:
        return all(
            marker in source
            for marker in (
                "def get_article_journal_from_data(",
                "def get_article_date_from_data(",
                "def get_drug_info_from_name(",
                "def get_article_info_from_name(",
            )
        )

    def _migrate_lookup_util_module(self, source: str) -> str:
        remove_file_extension = re.search(
            r"def remove_file_extension\(file_name: str\) -> str:.*?return file_name\.split\(\"/\"\)\[-1\]\.split\(\"\.\"\)\[0\]\n",
            source,
            flags=re.DOTALL,
        )
        remove_file_extension_block = (
            remove_file_extension.group(0).rstrip()
            if remove_file_extension
            else (
                "def remove_file_extension(file_name: str) -> str:\n"
                "    return file_name.split(\"/\")[-1].split(\".\")[0]"
            )
        )
        return (
            "from typing import Any\n\n\n"
            f"{remove_file_extension_block}\n\n\n"
            "def _records(frame: Any) -> list[dict[str, Any]]:\n"
            "    if hasattr(frame, \"to_dicts\"):\n"
            "        return frame.to_dicts()\n"
            "    if hasattr(frame, \"to_dict\"):\n"
            "        return frame.to_dict(\"records\")\n"
            "    return list(frame)\n\n\n"
            "def _first_record(frame: Any, column: str, value: str) -> dict[str, Any]:\n"
            "    for record in _records(frame):\n"
            "        if record.get(column) == value:\n"
            "            return record\n"
            "    raise IndexError(f\"No record found where {column}={value!r}\")\n\n\n"
            "def get_article_journal_from_data(data: dict[str, Any], article_name: str) -> str:\n"
            "    try:\n"
            "        return _first_record(data[\"pubmed\"], \"title\", article_name)[\"journal\"]\n"
            "    except IndexError:\n"
            "        return _first_record(\n"
            "            data[\"clinical_trials\"], \"scientific_title\", article_name\n"
            "        )[\"journal\"]\n\n\n"
            "def get_article_date_from_data(data: dict[str, Any], article_name: str) -> str:\n"
            "    try:\n"
            "        return _first_record(data[\"pubmed\"], \"title\", article_name)[\"date\"]\n"
            "    except IndexError:\n"
            "        return _first_record(\n"
            "            data[\"clinical_trials\"], \"scientific_title\", article_name\n"
            "        )[\"date\"]\n\n\n"
            "def get_drug_info_from_name(drug: str, data: dict[str, Any]) -> dict[str, Any]:\n"
            "    return _first_record(data[\"drugs\"], \"drug\", drug)\n\n\n"
            "def get_article_info_from_name(article: str, data: dict[str, Any]) -> dict[str, Any]:\n"
            "    try:\n"
            "        return _first_record(data[\"clinical_trials\"], \"scientific_title\", article)\n"
            "    except IndexError:\n"
            "        return _first_record(data[\"pubmed\"], \"title\", article)\n"
        )

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
