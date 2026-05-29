from __future__ import annotations

import json
import ast
import re
import urllib.request
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
        output = output.replace("pd.DataFrame(", "pl.DataFrame(")
        output = output.replace("pd.Series(", "pl.Series(")
        output = output.replace("pd.concat(", "pl.concat(")
        output = output.replace("pd.read_csv(", "pl.read_csv(")
        output = output.replace("pd.read_json(", "pl.read_json(")
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
        output = output.replace('.to_dict("records")', ".to_dicts()")
        output = output.replace(".to_dict('records')", ".to_dicts()")
        output = re.sub(r"\.groupby\(", ".group_by(", output)
        output = re.sub(r"\.drop_duplicates\(", ".unique(", output)
        output = re.sub(r"\.sort_values\(", ".sort(", output)
        output = re.sub(r"\.reset_index\(drop=True\)", "", output)
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

    def _migrate_requirements(self, source: str, step: dict[str, Any]) -> str:
        target_library = step.get("target_library")
        if not target_library:
            raise ValueError(
                f"Step {step['step_id']} targets requirements.txt, "
                "but no target_library was provided by diagnosis."
            )
        if self._requirements_contains_package(source, target_library):
            return source
        suffix = "" if source.endswith("\n") else "\n"
        if self._uses_hash_locked_requirements(source):
            dependency = self._resolve_hashed_requirement(target_library)
        else:
            dependency = target_library
        return source + suffix + dependency + "\n"

    def _requirements_contains_package(self, source: str, package_name: str) -> bool:
        normalized_package = _normalize_package_name(package_name)
        for raw_line in source.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            match = re.match(r"(?P<name>[A-Za-z0-9_.-]+)\s*(?P<constraint>.*)", line)
            if match and _normalize_package_name(match.group("name")) == normalized_package:
                return True
        return False

    def _uses_hash_locked_requirements(self, source: str) -> bool:
        return "--hash=sha256:" in source

    def _resolve_hashed_requirement(self, package_name: str) -> str:
        return "\n".join(
            self._resolve_hashed_requirement_blocks(package_name, version=None, seen=set())
        )

    def _resolve_hashed_requirement_blocks(
        self,
        package_name: str,
        version: str | None,
        seen: set[str],
    ) -> list[str]:
        metadata = self._fetch_pypi_package_metadata(package_name, version)
        version = metadata["info"]["version"]
        normalized_key = f"{_normalize_package_name(package_name)}=={version}"
        if normalized_key in seen:
            return []
        seen.add(normalized_key)

        files = metadata.get("releases", {}).get(version, metadata.get("urls", []))
        hashes = sorted({
            file_info.get("digests", {}).get("sha256")
            for file_info in files
            if file_info.get("digests", {}).get("sha256")
        })
        if not hashes:
            raise RuntimeError(
                f"Could not resolve sha256 hashes for {package_name}=={version} from PyPI."
            )

        lines = [f"{package_name}=={version} \\"]
        for index, digest in enumerate(hashes):
            continuation = " \\" if index < len(hashes) - 1 else ""
            lines.append(f"    --hash=sha256:{digest}{continuation}")
        blocks = ["\n".join(lines)]
        for requirement in metadata["info"].get("requires_dist") or []:
            pinned_dependency = _pinned_runtime_dependency(requirement)
            if not pinned_dependency:
                continue
            dependency_name, dependency_version = pinned_dependency
            blocks.extend(
                self._resolve_hashed_requirement_blocks(
                    dependency_name,
                    version=dependency_version,
                    seen=seen,
                )
            )
        return blocks

    def _fetch_pypi_package_metadata(
        self,
        package_name: str,
        version: str | None = None,
    ) -> dict[str, Any]:
        if version:
            url = f"https://pypi.org/pypi/{package_name}/{version}/json"
        else:
            url = f"https://pypi.org/pypi/{package_name}/json"
        with urllib.request.urlopen(url, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))


def _normalize_package_name(name: str) -> str:
    return name.replace("_", "-").lower()


def _pinned_runtime_dependency(requirement: str) -> tuple[str, str] | None:
    if ";" in requirement:
        return None
    match = re.match(
        r"(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[A-Za-z0-9_.!+*-]+)$",
        requirement.strip(),
    )
    if not match:
        return None
    return match.group("name"), match.group("version")
