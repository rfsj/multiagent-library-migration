from __future__ import annotations

import json
import ast
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.llm import get_llm, is_llm_timeout_error, with_structured_output
from src.tools.diff_analyzer import analyze_diff, changed_files
from src.tools.project_scanner import scan_project
from src.tools.test_runner import run_pytest

load_dotenv()

_PROMPTS_DIR = Path(__file__).parents[2] / "prompts"

# A rejection is first retried cheaply (deterministic implementation feedback).
# Only once those retries stop converging do we pay for the LLM verdict, which
# decides whether the fault is the implementation (retry) or the plan (replan).
LLM_VERDICT_ESCALATE_AFTER = 1

_HUMAN_TEMPLATE = """\
Review the migration step and return a structured verdict.

## Planned step
{planned_step}

## Migration result
{migration_result}

## Before snapshot
{before_snapshot}

## Validation evidence
{validation_evidence}
"""


class EvidenceSummary(BaseModel):
    tests_passed: bool = Field(default=False)
    out_of_scope_files: list[str] = Field(default_factory=list)
    imports_remaining: int = Field(default=0)
    api_calls_remaining: int = Field(default=0)
    upstream_skipped: bool = Field(default=False)


class ActionableFeedback(BaseModel):
    failure_location: str = Field(default="", description="file:line or empty.")
    failure_description: str = Field(default="", description="What went wrong.")
    suggested_correction: str = Field(default="", description="What should change.")


class ValidationVerdict(BaseModel):
    step_id: str = Field(description="Step identifier being reviewed.")
    verdict: Literal["accepted", "rejected_implementation", "rejected_plan"] = Field(
        description="Final decision for this migration step."
    )
    rationale: str = Field(description="Short explanation for the verdict.")
    feedback_target: Literal["agent_1", "agent_2", "none"] = Field(
        description="Which upstream agent should receive feedback."
    )
    feedback_for_agent: str = Field(
        description="Specific actionable feedback for the target agent, or empty string when accepted."
    )
    retry_recommendation: Literal["retry", "stop", "not_needed"] = Field(
        description="Whether the workflow should retry this step or stop."
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="Confidence level for the verdict."
    )
    evidence_summary: EvidenceSummary = Field(
        default_factory=EvidenceSummary,
        description="Structured summary of the deterministic evidence examined.",
    )
    actionable_feedback: ActionableFeedback = Field(
        default_factory=ActionableFeedback,
        description="Structured breakdown of the concrete problem and expected correction.",
    )


class ValidationAgent:
    """Independent validation agent for step checks, verdicts, and final checks."""

    name = "validation_agent"

    def __init__(self) -> None:
        self._chain = None

    def validate_step(
        self,
        project_dir: Path,
        step: dict[str, Any],
        before_dir: Path,
        logs_dir: Path,
    ) -> dict[str, Any]:
        logs_dir.mkdir(parents=True, exist_ok=True)
        changed = changed_files(before_dir, project_dir)
        allowed = set(step.get("allowed_files", []))
        out_of_scope = [path for path in changed if path not in allowed]
        self._install_dependencies(
            project_dir, logs_dir / f"{step['step_id']}_install.log"
        )
        tests = run_pytest(project_dir, logs_dir / f"{step['step_id']}_pytest.log")
        source_usage = self._source_usage_in_step_files(project_dir, step)
        missing_symbols = self._missing_public_symbols(project_dir, before_dir, step)
        result = {
            "agent": self.name,
            "step_id": step["step_id"],
            "changed_files": changed,
            "out_of_scope_changes": out_of_scope,
            "tests": tests["status"],
            "pytest_feedback": _pytest_failure_excerpt(tests),
            "old_imports_remaining": source_usage["old_imports_remaining"],
            "unmigrated_uses": source_usage["unmigrated_uses"],
            "missing_public_symbols": missing_symbols,
            "status": "approved"
            if not out_of_scope
            and tests["passed"]
            and source_usage["old_imports_remaining"] == 0
            and source_usage["unmigrated_uses"] == 0
            and not missing_symbols
            else "rejected",
        }
        (logs_dir / f"{step['step_id']}_validation.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        return result

    def evaluate_step(
        self,
        planned_step: dict[str, Any],
        migration_result: dict[str, Any],
        before_snapshot: dict[str, Any],
        validation_evidence: dict[str, Any],
        logs_dir: Path,
        retry_count: int = 0,
    ) -> dict[str, Any]:
        # Acceptances and the first rejections are decided deterministically: pytest
        # (the oracle) plus the AST scope/source-usage/missing-symbol checks already
        # settle them, with no LLM cost. Only once cheap implementation retries stop
        # converging (retry_count >= LLM_VERDICT_ESCALATE_AFTER) do we invoke the LLM
        # verdict, whose job is to attribute the failure: implementation (retry) vs
        # plan (replan). Semantic risks that tests cannot see are surfaced separately
        # by the post-validation semantic probe.
        logs_dir.mkdir(parents=True, exist_ok=True)
        if (
            validation_evidence.get("status") == "rejected"
            and retry_count >= LLM_VERDICT_ESCALATE_AFTER
        ):
            verdict = self._llm_step_verdict(
                planned_step, migration_result, before_snapshot, validation_evidence
            )
        else:
            verdict = self._deterministic_step_verdict(
                planned_step, migration_result, validation_evidence
            )
        (logs_dir / f"{planned_step['step_id']}_verdict.json").write_text(
            json.dumps(verdict, indent=2), encoding="utf-8"
        )
        return verdict

    def _llm_step_verdict(
        self,
        planned_step: dict[str, Any],
        migration_result: dict[str, Any],
        before_snapshot: dict[str, Any],
        validation_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Ask the LLM judge to attribute a non-converging rejection to the
        implementation (``rejected_implementation`` → retry) or the plan
        (``rejected_plan`` → replan). Falls back to a deterministic implementation
        rejection if the model returns no structured output."""
        try:
            result = self._get_chain().invoke(
                {
                    "planned_step": json.dumps(planned_step, indent=2, sort_keys=True),
                    "migration_result": json.dumps(
                        migration_result, indent=2, sort_keys=True
                    ),
                    "before_snapshot": json.dumps(
                        before_snapshot, indent=2, sort_keys=True
                    ),
                    "validation_evidence": json.dumps(
                        validation_evidence, indent=2, sort_keys=True
                    ),
                }
            )
        except Exception as exc:
            if not is_llm_timeout_error(exc):
                raise
            return self._deterministic_step_verdict(
                planned_step, migration_result, validation_evidence
            )
        if not isinstance(result, ValidationVerdict):
            return self._deterministic_step_verdict(
                planned_step, migration_result, validation_evidence
            )
        verdict = {"agent": self.name}
        verdict.update(result.model_dump())
        return verdict

    def _get_chain(self):
        if self._chain is None:
            system_prompt = (_PROMPTS_DIR / "validation_agent_v2.md").read_text(
                encoding="utf-8"
            )
            llm = with_structured_output(get_llm(), ValidationVerdict)
            self._chain = (
                ChatPromptTemplate.from_messages(
                    [
                        SystemMessage(content=system_prompt),
                        ("human", _HUMAN_TEMPLATE),
                    ]
                )
                | llm
            )
        return self._chain

    def final_validate(
        self,
        project_dir: Path,
        before_dir: Path,
        logs_dir: Path,
        source_library: str,
        allowed_files: list[str] | None = None,
    ) -> dict[str, Any]:
        self._install_dependencies(project_dir, logs_dir / "final_install.log")
        scan = scan_project(project_dir, source_library)
        diff = analyze_diff(before_dir, project_dir, allowed_files=allowed_files)
        tests = run_pytest(project_dir, logs_dir / "final_pytest.log")
        old_imports_remaining = len(scan["source_imports_in_source"])
        unmigrated_uses = len(scan["source_api_calls_in_source"])
        result = {
            "agent": self.name,
            "tests": tests["status"],
            "old_imports_remaining": old_imports_remaining,
            "unmigrated_uses": unmigrated_uses,
            "test_old_imports_remaining": len(scan["source_imports_in_tests"]),
            "test_source_api_calls_remaining": len(scan["source_api_calls_in_tests"]),
            "out_of_scope_changes": diff["out_of_scope_changes"],
            "out_of_scope_files": diff["out_of_scope_files"],
            "allowed_files": allowed_files,
            "test_usage_policy": (
                "Source-library usage in tests is allowed during final validation."
            ),
            "status": "approved"
            if tests["passed"]
            and old_imports_remaining == 0
            and unmigrated_uses == 0
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

    def _source_usage_in_step_files(
        self, project_dir: Path, step: dict[str, Any]
    ) -> dict[str, int]:
        source_library = step.get("source_library")
        rel_files = step.get("files") or [step["file"]]
        if not source_library:
            return {"old_imports_remaining": 0, "unmigrated_uses": 0}

        totals = {"old_imports_remaining": 0, "unmigrated_uses": 0}
        for rel_file in rel_files:
            usage = self._source_usage_in_one_step_file(
                project_dir,
                Path(rel_file),
                step,
                source_library,
            )
            totals["old_imports_remaining"] += usage["old_imports_remaining"]
            totals["unmigrated_uses"] += usage["unmigrated_uses"]
        return totals

    def _source_usage_in_one_step_file(
        self,
        project_dir: Path,
        rel_file: Path,
        step: dict[str, Any],
        source_library: str,
    ) -> dict[str, int]:
        if rel_file.suffix != ".py":
            return {"old_imports_remaining": 0, "unmigrated_uses": 0}

        path = project_dir / rel_file
        if not path.exists():
            return {"old_imports_remaining": 0, "unmigrated_uses": 0}

        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            return {"old_imports_remaining": 0, "unmigrated_uses": 0}

        allowed_symbols = step.get("allowed_symbols", [])
        return _ast_count_source_usage(tree, source_library, allowed_symbols)

    def _missing_public_symbols(
        self,
        project_dir: Path,
        before_dir: Path,
        step: dict[str, Any],
    ) -> list[str]:
        """Top-level public functions/classes present before the step but dropped
        by the migration. Deterministic safety net against an agent that deletes or
        renames a public symbol (which breaks tests or downstream imports)."""
        missing: set[str] = set()
        for rel in step.get("files") or [step["file"]]:
            rel = Path(rel)
            if rel.suffix != ".py":
                continue
            before = before_dir / rel
            after = project_dir / rel
            if not before.exists() or not after.exists():
                continue
            missing.update(
                _missing_top_level_symbols(
                    before.read_text(encoding="utf-8"),
                    after.read_text(encoding="utf-8"),
                )
            )
        return sorted(missing)

    def _deterministic_step_verdict(
        self,
        planned_step: dict[str, Any],
        migration_result: dict[str, Any],
        validation_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        step_id = planned_step["step_id"]
        if validation_evidence.get("status") == "approved":
            return {
                "agent": self.name,
                "step_id": step_id,
                "verdict": "accepted",
                "rationale": (
                    "Tests passed, no out-of-scope changes were detected, no "
                    "source-library usage remains in the migrated scope, and no "
                    "public symbols were dropped."
                ),
                "feedback_target": "none",
                "feedback_for_agent": "",
                "retry_recommendation": "not_needed",
                "confidence": "high",
            }

        feedback = {
            **validation_evidence,
            "actionable_feedback": _actionable_validation_feedback(validation_evidence),
        }
        return {
            "agent": self.name,
            "step_id": step_id,
            "verdict": "rejected_implementation",
            "rationale": (
                "The step failed validation evidence: tests, scope, remaining "
                "source-library usage, or a dropped public symbol did not satisfy "
                "the migration contract."
            ),
            "feedback_target": "agent_2",
            "feedback_for_agent": json.dumps(feedback, sort_keys=True),
            "retry_recommendation": "retry",
            "confidence": "high",
        }


def _ast_library_aliases(tree: ast.Module, source_library: str) -> set[str]:
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == source_library or alias.name.startswith(
                    f"{source_library}."
                ):
                    aliases.add(alias.asname or alias.name.split(".")[0])
    return aliases


def _ast_node_imports_library(node: ast.AST, source_library: str) -> bool:
    if isinstance(node, ast.Import):
        return any(
            alias.name == source_library or alias.name.startswith(f"{source_library}.")
            for alias in node.names
        )
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        return module == source_library or module.startswith(f"{source_library}.")
    return False


# Well-known short aliases per library used as a fallback: catches references
# that remain after the import was removed (partial migration produces code
# like `pd.read_csv(...)` with no `import pandas as pd`).
_COMMON_LIBRARY_ALIASES: dict[str, set[str]] = {
    "pandas": {"pd", "pandas"},
    "polars": {"pl", "polars"},
}


def _ast_count_source_usage(
    tree: ast.Module,
    source_library: str,
    allowed_symbols: list[str],
) -> dict[str, int]:
    aliases = _ast_library_aliases(tree, source_library)
    # Include well-known short aliases even when the import is absent, so a
    # partially-migrated file that removed the import but left `pd.` calls is
    # still detected as having unmigrated uses.
    aliases |= _COMMON_LIBRARY_ALIASES.get(source_library, {source_library})

    if allowed_symbols:
        symbol_set = set(allowed_symbols)
        check_nodes: list[ast.AST] = []
        for node in tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name in symbol_set
            ):
                check_nodes.extend(ast.walk(node))
        old_imports = sum(
            1 for node in check_nodes if _ast_node_imports_library(node, source_library)
        )
        unmigrated_uses = sum(
            1
            for node in check_nodes
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in aliases
        )
    else:
        walk = list(ast.walk(tree))
        old_imports = sum(
            1 for node in walk if _ast_node_imports_library(node, source_library)
        )
        unmigrated_uses = sum(
            1
            for node in walk
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in aliases
        )

    return {"old_imports_remaining": old_imports, "unmigrated_uses": unmigrated_uses}


_PYTEST_FAILURE_PATTERNS = frozenset(
    {
        "AttributeError:",
        "TypeError:",
        "ValueError:",
        "AssertionError:",
        "ImportError:",
        "ModuleNotFoundError:",
        # Polars-specific
        "ColumnNotFoundError:",
        "InvalidOperationError:",
        "SchemaError:",
        "ComputeError:",
    }
)


def _pytest_failure_excerpt(tests: dict[str, Any], max_lines: int = 80) -> str:
    if tests.get("passed"):
        return ""
    log_file = tests.get("log_file")
    if not log_file:
        return ""
    path = Path(log_file)
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    failure_lines = [
        line
        for line in lines
        if (
            line.startswith("E       ")
            or "FAILED " in line
            or any(pattern in line for pattern in _PYTEST_FAILURE_PATTERNS)
        )
    ]
    selected = failure_lines[-max_lines:] if failure_lines else lines[-max_lines:]
    return "\n".join(selected)


def _missing_top_level_symbols(original_code: str, migrated_code: str) -> list[str]:
    return sorted(
        _top_level_public_symbols(original_code)
        - _top_level_public_symbols(migrated_code)
    )


def _top_level_public_symbols(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not node.name.startswith("_")
    }


def _actionable_validation_feedback(validation_evidence: dict[str, Any]) -> str:
    missing_symbols = validation_evidence.get("missing_public_symbols") or []
    pytest_feedback = validation_evidence.get("pytest_feedback", "")
    if not pytest_feedback:
        if missing_symbols:
            return (
                "Restore every missing top-level public function/class from the "
                "original file and migrate its implementation instead of deleting "
                f"or renaming it. Missing symbols: {', '.join(missing_symbols)}."
            )
        return "Review validation evidence and revise the implementation."
    hints = []
    if missing_symbols:
        hints.append(
            "Restore the missing top-level public symbols and migrate their bodies "
            f"instead of dropping them: {', '.join(missing_symbols)}."
        )
    has_assertion_index_diff = (
        "At index" in pytest_feedback and " diff" in pytest_feedback
    )
    has_list_like_diff = "[" in pytest_feedback and "]" in pytest_feedback
    if "does not support `Series` assignment by index" in pytest_feedback:
        hints.append(
            "The migrated code is assigning columns with pandas syntax on a "
            'Polars DataFrame. Replace df["col"] = ... with df = '
            'df.with_columns(...alias("col")).'
        )
    if (
        "object has no attribute 'sort'" in pytest_feedback
        or "object has no attribute 'with_columns'" in pytest_feedback
        or "object has no attribute 'group_by'" in pytest_feedback
    ):
        hints.append(
            "The migrated code is using Polars APIs on an object that is still a "
            "pandas DataFrame. Preserve producer/consumer type compatibility or "
            "migrate the upstream producer first."
        )
    if "ColumnNotFoundError" in pytest_feedback:
        hints.append(
            "A Polars expression referenced a missing column. If a new column is "
            "created and then used by another new column, split the expressions "
            "into sequential with_columns calls."
        )
    if "reset_index" in pytest_feedback:
        hints.append(
            "The migrated code is still using pandas reset_index on a Polars "
            "DataFrame. Remove reset_index(drop=True); Polars has no pandas-style "
            "row index in the DataFrame contract."
        )
    if "unexpected keyword argument 'ascending'" in pytest_feedback:
        hints.append(
            "The migrated code passed pandas ascending= to Polars sort. Polars "
            "uses descending= with inverted booleans, for example pandas "
            "ascending=[False, False, True] becomes descending=[True, True, False]."
        )
    if has_assertion_index_diff:
        hints.append(
            "The migrated code has a semantic mismatch in row order or selected "
            "rows. Compare each original pandas sort_values(..., ascending=...) "
            "with the Polars sort(..., descending=...) equivalent, preserve null "
            "ordering, and when pandas sorted before drop_duplicates(..., "
            "keep='first'), use unique(..., keep='first', maintain_order=True)."
        )
    if "columns" in pytest_feedback or (
        has_assertion_index_diff and has_list_like_diff
    ):
        hints.append(
            "The migrated output may have a column-order mismatch. If this comes "
            "from a pivot/table reshape, preserve the original index columns and "
            "explicitly select pivoted value columns in the expected deterministic "
            "order before returning."
        )
    if (
        ": None" in pytest_feedback
        or ": null" in pytest_feedback
        or "None}" in pytest_feedback
    ):
        hints.append(
            "The migrated output includes a null grouping/index value. If the "
            "original pandas operation dropped null groups, filter null values "
            "from the relevant grouping or pivot index column before the Polars "
            "operation."
        )
    if not hints:
        hints.append("Use the pytest failure excerpt to revise the implementation.")
    return " ".join(hints)
