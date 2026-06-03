from __future__ import annotations

import json
import ast
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.llm import get_llm
from src.tools.diff_analyzer import analyze_diff, changed_files
from src.tools.project_scanner import scan_project
from src.tools.test_runner import run_pytest

load_dotenv()

_PROMPTS_DIR = Path(__file__).parents[2] / "prompts"

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
        self._install_dependencies(project_dir, logs_dir / f"{step['step_id']}_install.log")
        tests = run_pytest(project_dir, logs_dir / f"{step['step_id']}_pytest.log")
        source_usage = self._source_usage_in_step_file(project_dir, step)
        result = {
            "agent": self.name,
            "step_id": step["step_id"],
            "changed_files": changed,
            "out_of_scope_changes": out_of_scope,
            "tests": tests["status"],
            "old_imports_remaining": source_usage["old_imports_remaining"],
            "unmigrated_uses": source_usage["unmigrated_uses"],
            "status": "approved"
            if not out_of_scope
            and tests["passed"]
            and source_usage["old_imports_remaining"] == 0
            and source_usage["unmigrated_uses"] == 0
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
    ) -> dict[str, Any]:
        logs_dir.mkdir(parents=True, exist_ok=True)
        deterministic_verdict = self._deterministic_step_verdict(
            planned_step,
            migration_result,
            validation_evidence,
        )
        if deterministic_verdict is not None:
            (logs_dir / f"{planned_step['step_id']}_verdict.json").write_text(
                json.dumps(deterministic_verdict, indent=2), encoding="utf-8"
            )
            return deterministic_verdict

        result = self._get_chain().invoke({
            "planned_step": json.dumps(planned_step, indent=2, sort_keys=True),
            "migration_result": json.dumps(migration_result, indent=2, sort_keys=True),
            "before_snapshot": json.dumps(before_snapshot, indent=2, sort_keys=True),
            "validation_evidence": json.dumps(validation_evidence, indent=2, sort_keys=True),
        })
        verdict_payload = result.model_dump() if isinstance(result, ValidationVerdict) else dict(result)
        verdict = {"agent": self.name}
        verdict.update(verdict_payload)
        (logs_dir / f"{planned_step['step_id']}_verdict.json").write_text(
            json.dumps(verdict, indent=2), encoding="utf-8"
        )
        return verdict

    def final_validate(
        self,
        project_dir: Path,
        before_dir: Path,
        logs_dir: Path,
        source_library: str,
        allowed_files: list[str] | None = None,
    ) -> dict[str, Any]:
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

    def _source_usage_in_step_file(self, project_dir: Path, step: dict[str, Any]) -> dict[str, int]:
        source_library = step.get("source_library")
        rel_file = Path(step["file"])
        if not source_library or rel_file.suffix != ".py":
            return {"old_imports_remaining": 0, "unmigrated_uses": 0}

        path = project_dir / rel_file
        if not path.exists():
            return {"old_imports_remaining": 0, "unmigrated_uses": 0}

        content = path.read_text(encoding="utf-8")
        allowed_symbols = step.get("allowed_symbols", [])
        if allowed_symbols:
            content = _source_for_symbols(content, allowed_symbols)
            old_imports = content.count(f"import {source_library}") + content.count(
                f"from {source_library} import"
            )
            alias_uses = content.count("pd.") if source_library == "pandas" else 0
            direct_uses = content.count(f"{source_library}.")
            return {
                "old_imports_remaining": old_imports,
                "unmigrated_uses": alias_uses + direct_uses,
            }

        old_imports = content.count(f"import {source_library}") + content.count(
            f"from {source_library} import"
        )
        alias_uses = content.count("pd.") if source_library == "pandas" else 0
        direct_uses = content.count(f"{source_library}.")
        return {
            "old_imports_remaining": old_imports,
            "unmigrated_uses": alias_uses + direct_uses,
        }

    def _deterministic_step_verdict(
        self,
        planned_step: dict[str, Any],
        migration_result: dict[str, Any],
        validation_evidence: dict[str, Any],
    ) -> dict[str, Any] | None:
        step_id = planned_step["step_id"]
        if validation_evidence.get("status") == "approved" and migration_result.get("changed"):
            return {
                "agent": self.name,
                "step_id": step_id,
                "verdict": "accepted",
                "rationale": (
                    "The step changed the planned files, tests passed, no out-of-scope "
                    "changes were detected, and no source-library usage remains in the "
                    "migrated file."
                ),
                "feedback_target": "none",
                "feedback_for_agent": "",
                "retry_recommendation": "not_needed",
                "confidence": "high",
            }

        if validation_evidence.get("status") == "rejected":
            return {
                "agent": self.name,
                "step_id": step_id,
                "verdict": "rejected_implementation",
                "rationale": (
                    "The step failed validation evidence: tests, scope, or remaining "
                    "source-library usage did not satisfy the migration contract."
                ),
                "feedback_target": "agent_2",
                "feedback_for_agent": json.dumps(validation_evidence, sort_keys=True),
                "retry_recommendation": "retry",
                "confidence": "high",
            }

        return None

    def _get_chain(self):
        if self._chain is None:
            system_prompt = (_PROMPTS_DIR / "validation_agent_v1.md").read_text(encoding="utf-8")
            llm = get_llm().with_structured_output(ValidationVerdict)
            self._chain = (
                ChatPromptTemplate.from_messages([
                    ("system", system_prompt),
                    ("human", _HUMAN_TEMPLATE),
                ])
                | llm
            )
        return self._chain


def _source_for_symbols(source: str, symbols: list[str]) -> str:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    lines = source.splitlines(keepends=True)
    parts = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if node.name in symbols and hasattr(node, "end_lineno"):
            parts.append("".join(lines[node.lineno - 1:node.end_lineno]))
    return "\n".join(parts) if parts else source
