from __future__ import annotations

import ast
import json
import re
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.agents.implementation_review_agent import ImplementationReviewAgent
from src.llm import get_llm

load_dotenv()

_PROMPTS_DIR = Path(__file__).parents[2] / "prompts"
MAX_IMPLEMENTATION_REVIEW_REVISIONS = 2
MAX_MIGRATION_STRUCTURED_OUTPUT_ATTEMPTS = 2

_HUMAN_TEMPLATE = """\
Migrate the following file from {source_library} to {target_library}.

## Planned step
{description}

## File info
- File: {file}
- Allowed symbols to migrate: {allowed_symbols_str}

## Source code
```python
{source_code}
```

## Key Points
- Complete the migration fully. Every use of {source_library} must be replaced.
- Use idiomatic {target_library} code (e.g., `.filter()` instead of boolean indexing).
- Delete lines that don't apply in {target_library} (e.g., `.reset_index(drop=True)` for polars).
- Ensure the code is syntactically valid for Python 3.9+.
- Preserve all business logic and behavior.

{retry_feedback_context}

Return ONLY the complete migrated file, preserving all untouched code outside the migration scope.
"""


class MigrationResult(BaseModel):
    migrated_code: str = Field(
        description="The complete migrated file content, preserving all untouched code."
    )
    changes_summary: str = Field(
        description="Brief summary of the migration changes applied."
    )


class MigrationAgent:
    """LLM-powered agent that executes one planned migration step at a time."""

    name = "migration_agent"

    def __init__(self, implementation_review_agent: ImplementationReviewAgent | None = None) -> None:
        system_prompt = (_PROMPTS_DIR / "migration_agent_v1.md").read_text(encoding="utf-8")
        llm = get_llm().with_structured_output(MigrationResult)
        self._chain = (
            ChatPromptTemplate.from_messages([
                ("system", system_prompt),
                ("human", _HUMAN_TEMPLATE),
            ])
            | llm
        )
        self._implementation_review_agent = (
            implementation_review_agent or ImplementationReviewAgent()
        )


    def run_step(self, project_dir: Path, step: dict[str, Any], logs_dir: Path) -> dict[str, Any]:
        logs_dir.mkdir(parents=True, exist_ok=True)
        self._migration_structured_output_attempts = 0
        self._migration_structured_output_error = ""
        if step.get("files"):
            return self._run_grouped_step(project_dir, step, logs_dir)

        rel_file = Path(step["file"])
        self._validate_step_scope(step, rel_file)
        target = project_dir / rel_file
        original = target.read_text(encoding="utf-8")
        
        # Invoke LLM to migrate the file
        retry_feedback = step.get("retry_feedback")
        migrated = self._migrate_file_with_llm(
            rel_file,
            original,
            step,
            retry_feedback,
            logs_dir,
        )
        
        changed_files: list[str] = []
        if migrated != original:
            target.write_text(migrated, encoding="utf-8")
            changed_files.append(str(rel_file))

        # Handle requirements.txt migration if needed
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
        if self._migration_structured_output_attempts:
            result["structured_output_attempts"] = (
                self._migration_structured_output_attempts
            )
        if self._migration_structured_output_error:
            result["structured_output_error"] = self._migration_structured_output_error
        (logs_dir / f"{step['step_id']}_migration.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        return result

    def _run_grouped_step(
        self,
        project_dir: Path,
        step: dict[str, Any],
        logs_dir: Path,
    ) -> dict[str, Any]:
        changed_files: list[str] = []
        file_results: list[dict[str, Any]] = []
        for rel_file_str in step.get("files", []):
            file_step = {
                **step,
                "file": rel_file_str,
                "allowed_symbols": [],
            }
            rel_file = Path(rel_file_str)
            self._validate_step_scope(file_step, rel_file)
            target = project_dir / rel_file
            original = target.read_text(encoding="utf-8")
            migrated = self._migrate_file_with_llm(
                rel_file,
                original,
                file_step,
                step.get("retry_feedback"),
                logs_dir,
            )
            changed = migrated != original
            if changed:
                target.write_text(migrated, encoding="utf-8")
                changed_files.append(str(rel_file))
            file_results.append(
                {
                    "file": str(rel_file),
                    "changed": changed,
                    "structured_output_attempts": self._migration_structured_output_attempts,
                    "structured_output_error": self._migration_structured_output_error,
                }
            )

        if "requirements.txt" in step.get("allowed_files", []):
            if self._migrate_allowed_requirements(project_dir, step):
                changed_files.append("requirements.txt")

        result = {
            "agent": self.name,
            "step_id": step["step_id"],
            "file": step["file"],
            "files": step.get("files", []),
            "allowed_symbols": [],
            "changed": bool(changed_files),
            "changed_files": changed_files,
            "file_results": file_results,
            "status": "completed" if changed_files else "no_change",
            "retry_feedback_received": bool(step.get("retry_feedback")),
        }
        (logs_dir / f"{step['step_id']}_migration.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        return result

    def _migrate_file_with_llm(
        self,
        rel_file: Path,
        source: str,
        step: dict[str, Any],
        retry_feedback: dict[str, Any] | str | None,
        logs_dir: Path,
    ) -> str:
        if rel_file.name == "requirements.txt":
            return self._migrate_requirements(source, step)
        
        if rel_file.suffix != ".py":
            return source

        allowed_symbols = step.get("allowed_symbols", [])
        migrated = self._invoke_migration_chain(rel_file, source, step, retry_feedback)
        if allowed_symbols:
            migrated = self._apply_allowed_symbol_scope(source, migrated, allowed_symbols)

        migrated = self._regenerate_if_invalid_python(
            rel_file, source, step, migrated, allowed_symbols
        )

        revision_index = 0
        review = self._review_migrated_code(rel_file, source, migrated, step, logs_dir)
        while (
            review
            and review["status"] == "needs_revision"
            and revision_index < MAX_IMPLEMENTATION_REVIEW_REVISIONS
        ):
            revision_index += 1
            migrated = self._invoke_migration_chain(
                rel_file,
                source,
                step,
                {
                    "feedback_for_agent": _review_feedback_for_migration(
                        review, migrated
                    )
                },
            )
            if allowed_symbols:
                migrated = self._apply_allowed_symbol_scope(
                    source, migrated, allowed_symbols
                )
            migrated = self._regenerate_if_invalid_python(
                rel_file, source, step, migrated, allowed_symbols
            )
            suffix = (
                "implementation_review_after_revision"
                if revision_index == 1
                else f"implementation_review_after_revision_{revision_index}"
            )
            review = self._review_migrated_code(
                rel_file,
                source,
                migrated,
                step,
                logs_dir,
                log_suffix=suffix,
            )

        return migrated

    def _regenerate_if_invalid_python(
        self,
        rel_file: Path,
        source: str,
        step: dict[str, Any],
        migrated: str,
        allowed_symbols: list[str],
    ) -> str:
        syntax_error = self._validate_python39_syntax(migrated)
        if not syntax_error:
            return migrated
        regenerated = self._invoke_migration_chain(
            rel_file,
            source,
            step,
            {
                "feedback_for_agent": (
                    "The previous migrated file was not valid Python and "
                    "must be regenerated as a complete, syntactically valid "
                    f"file. Syntax feedback: {syntax_error}"
                )
            },
        )
        if allowed_symbols:
            regenerated = self._apply_allowed_symbol_scope(
                source, regenerated, allowed_symbols
            )
        return regenerated

    def _invoke_migration_chain(
        self,
        rel_file: Path,
        source: str,
        step: dict[str, Any],
        retry_feedback: dict[str, Any] | str | None,
    ) -> str:
        allowed_symbols = step.get("allowed_symbols", [])
        allowed_symbols_str = ", ".join(allowed_symbols) if allowed_symbols else "(all code in file)"

        retry_feedback_context = ""
        if retry_feedback:
            retry_feedback_context = _retry_feedback_context(retry_feedback)

        prompt_payload = {
            "source_library": step.get("source_library", "pandas"),
            "target_library": step.get("target_library", "polars"),
            "file": str(rel_file),
            "description": step.get("description", "Migrate this file."),
            "allowed_symbols_str": allowed_symbols_str,
            "source_code": source,
            "retry_feedback_context": retry_feedback_context,
        }
        for attempt in range(1, MAX_MIGRATION_STRUCTURED_OUTPUT_ATTEMPTS + 1):
            result: MigrationResult | None = self._chain.invoke(prompt_payload)
            self._migration_structured_output_attempts = attempt
            migrated_code = getattr(result, "migrated_code", None)
            if isinstance(migrated_code, str):
                self._migration_structured_output_error = ""
                return migrated_code

        self._migration_structured_output_error = (
            "MigrationAgent returned no structured output."
        )
        return source

    def _review_migrated_code(
        self,
        rel_file: Path,
        original: str,
        migrated: str,
        step: dict[str, Any],
        logs_dir: Path,
        log_suffix: str = "implementation_review",
    ) -> dict[str, Any] | None:
        dataframe_flow_analysis = step.get("dataframe_flow_analysis")
        if not dataframe_flow_analysis:
            return None
        if rel_file.suffix != ".py":
            return None
        if migrated == original:
            return None
        return self._implementation_review_agent.review(
            rel_file=rel_file,
            original_code=original,
            migrated_code=migrated,
            planned_step=step,
            dataframe_flow_analysis=dataframe_flow_analysis,
            logs_dir=logs_dir,
            log_suffix=log_suffix,
        )

    def _apply_allowed_symbol_scope(
        self,
        original: str,
        migrated: str,
        allowed_symbols: list[str],
    ) -> str:
        try:
            original_tree = ast.parse(original)
            migrated_tree = ast.parse(migrated)
        except SyntaxError:
            return original

        migrated_symbols = {
            node.name: node
            for node in migrated_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and hasattr(node, "end_lineno")
        }
        if not migrated_symbols:
            return original

        original_lines = original.splitlines(keepends=True)
        migrated_lines = migrated.splitlines(keepends=True)
        replacements: list[tuple[int, int, str]] = []
        for node in original_tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if node.name not in allowed_symbols or not hasattr(node, "end_lineno"):
                continue
            migrated_node = migrated_symbols.get(node.name)
            if not migrated_node:
                continue
            replacement = "".join(
                migrated_lines[migrated_node.lineno - 1 : migrated_node.end_lineno]
            )
            replacements.append((node.lineno - 1, node.end_lineno, replacement))

        if not replacements:
            return original

        for start, end, replacement in reversed(replacements):
            original_lines[start:end] = [replacement]
        scoped = "".join(original_lines)
        if "pl." in scoped and "import polars as pl" not in scoped:
            scoped = self._ensure_polars_import(scoped)
        scoped = self._remove_unused_pandas_alias_import(scoped)
        return scoped

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



    def _validate_python39_syntax(self, code: str) -> str | None:
        """
        Validate that code is syntactically compatible with Python 3.9.
        Returns None if valid, or error message string if invalid.
        
        Catches common issues like PEP 604 union syntax (str | Path)
        which is only available in Python 3.10+.
        """
        try:
            ast.parse(code)
            return None  # Valid syntax
        except SyntaxError as e:
            error_msg = f"Syntax error on line {e.lineno}: {e.msg}"
            
            # Detect PEP 604 union syntax error
            if "invalid syntax (<|>" in str(e) or "unsupported operand type" in str(e):
                error_msg = (
                    f"Line {e.lineno}: PEP 604 union syntax (X | Y) not supported in Python 3.9. "
                    f"Use Union[X, Y] from typing module instead. Error: {e.msg}"
                )
            
            return error_msg



    def _validate_step_scope(self, step: dict[str, Any], rel_file: Path) -> None:
        allowed_files = set(step.get("allowed_files", []))
        if str(rel_file) not in allowed_files:
            raise ValueError(
                f"Step {step['step_id']} targets {rel_file}, "
                "but that file is not listed in allowed_files."
            )

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


def _review_feedback_for_migration(review: dict[str, Any], migrated_code: str) -> str:
    return (
        "Implementation review requested a revision before validation.\n\n"
        f"Issues:\n{json.dumps(review.get('issues', []), indent=2)}\n\n"
        f"Revision instructions:\n{review.get('revision_instructions', '')}\n\n"
        "Previous migrated code:\n"
        "```python\n"
        f"{migrated_code}\n"
        "```"
    )


def _retry_feedback_context(retry_feedback: dict[str, Any] | str) -> str:
    if not isinstance(retry_feedback, dict):
        return (
            "\n## Retry feedback from validation or implementation review\n"
            f"{retry_feedback}"
        )

    feedback_text = retry_feedback.get("feedback_for_agent", "No specific feedback.")
    repair_plan = retry_feedback.get("repair_plan")
    validation_feedback = retry_feedback.get("validation_feedback")
    parts = [
        "## Retry feedback from validation or implementation review",
        str(feedback_text),
    ]
    if repair_plan:
        parts.extend(
            [
                "",
                "## Structured Repair Plan",
                "Treat this plan as mandatory for the next migrated file. "
                "Before returning code, verify every acceptance criterion and "
                "avoid every forbidden pattern.",
                json.dumps(repair_plan, indent=2, sort_keys=True),
            ]
        )
        acceptance = repair_plan.get("acceptance_criteria") or []
        if acceptance:
            parts.extend(
                [
                    "",
                    "## Mandatory Acceptance Criteria",
                    *[f"- {item}" for item in acceptance],
                ]
            )
        must_not_do = repair_plan.get("must_not_do") or []
        if must_not_do:
            parts.extend(
                [
                    "",
                    "## Forbidden Patterns For This Retry",
                    *[f"- {item}" for item in must_not_do],
                ]
            )
    if validation_feedback:
        parts.extend(
            [
                "",
                "## Original Validation Feedback",
                str(validation_feedback),
            ]
        )
    return "\n" + "\n".join(parts)


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
