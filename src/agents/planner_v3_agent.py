from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.llm import get_llm
from src.tools.project_scanner import build_project_audit, scan_project

load_dotenv()

_PROMPTS_DIR = Path(__file__).parents[2] / "prompts"

_HUMAN_TEMPLATE = """\
Analyze the project below and produce a migration plan from \
{source_library} to {target_library}.

## Source files with {source_library} usage

{file_contents}

## Structural metadata (from static analysis)

- Dependency files: {dependency_files}
- Test files: {test_files}
- Affected files: {affected_files}

{replan_context}
"""


class PlannerV3Step(BaseModel):
    step_id: str = Field(description="Unique step identifier: step_001, step_002, ...")
    file: str = Field(description="File path relative to the repository root.")
    description: str = Field(description="Human-readable migration intent.")
    allowed_files: list[str] = Field(description="Files this step may modify.")
    allowed_symbols: list[str] = Field(
        default_factory=list,
        description="Optional top-level symbols this step may modify.",
    )
    files: list[str] = Field(
        default_factory=list,
        description="Grouped files, empty for normal single-file steps.",
    )
    status: str = Field(default="planned", description="Always planned.")


class PlannerV3Plan(BaseModel):
    source_library: str = Field(description="Library being migrated from.")
    target_library: str = Field(description="Library being migrated to.")
    dependency_files: list[str] = Field(description="Dependency files found.")
    affected_files: list[str] = Field(description="Production files to migrate.")
    related_tests: list[str] = Field(description="Related test files.")
    complexity: dict[str, str] = Field(
        description="Complexity per affected file: low, medium, or high."
    )
    migration_steps: list[PlannerV3Step] = Field(description="Ordered migration steps.")


class PlannerV3Agent:
    """Primary planner agent for the migration workflow.

    This is the planner surface to evolve. It owns project diagnosis,
    migration-step planning, downstream scope contracts, and auditable planner
    logs.
    """

    name = "diagnosis_agent"

    def __init__(self) -> None:
        system_prompt = (_PROMPTS_DIR / "diagnosis_agent_v3.md").read_text(
            encoding="utf-8"
        )
        llm = get_llm().with_structured_output(PlannerV3Plan)
        self._chain = (
            ChatPromptTemplate.from_messages([
                ("system", system_prompt),
                ("human", _HUMAN_TEMPLATE),
            ])
            | llm
        )

    def run(
        self,
        project_dir: Path,
        logs_dir: Path,
        source_library: str,
        target_library: str,
        replan_feedback: dict[str, Any] | None = None,
        replan_attempt: int = 0,
    ) -> dict[str, Any]:
        logs_dir.mkdir(parents=True, exist_ok=True)
        scan = scan_project(project_dir, source_library)
        audit = build_project_audit(project_dir, source_library, target_library)
        affected_source_files = scan["affected_source_files"]

        audit_log_name = (
            "project_audit.json"
            if replan_attempt == 0
            else f"project_audit_replan_{replan_attempt}.json"
        )
        (logs_dir / audit_log_name).write_text(
            json.dumps(audit, indent=2),
            encoding="utf-8",
        )

        result: PlannerV3Plan = self._chain.invoke({
            "source_library": source_library,
            "target_library": target_library,
            "file_contents": self._collect_file_contents(project_dir, affected_source_files),
            "dependency_files": scan["dependency_files"],
            "test_files": scan["test_files"],
            "affected_files": affected_source_files,
            "replan_context": self._build_replan_context(
                replan_feedback,
                replan_attempt,
            ),
        })
        if result is None:
            raise RuntimeError("PlannerV3Agent did not return a structured plan.")

        migration_steps, warnings = self._normalize_steps(
            result.migration_steps,
            affected_source_files,
            scan["dependency_files"],
            audit["dependency_summary"],
        )

        plan = {
            "agent": self.name,
            "planner_version": "v3",
            "source_library": result.source_library,
            "target_library": result.target_library,
            "read_only": True,
            "dependency_files": result.dependency_files,
            "dependency_summary": audit["dependency_summary"],
            "affected_files": result.affected_files,
            "affected_source_files": affected_source_files,
            "test_files_with_source_library_usage": scan[
                "test_files_with_source_library_usage"
            ],
            "related_tests": result.related_tests,
            "complexity": result.complexity,
            "dataframe_flow_analysis": {"symbols": [], "groups": [], "notes": []},
            "planner_warnings": warnings,
            "migration_steps": migration_steps,
        }

        log_name = (
            "diagnosis_plan.json"
            if replan_attempt == 0
            else f"diagnosis_plan_replan_{replan_attempt}.json"
        )
        (logs_dir / log_name).write_text(
            json.dumps(plan, indent=2),
            encoding="utf-8",
        )
        return plan

    def _collect_file_contents(self, project_dir: Path, affected_files: list[str]) -> str:
        if not affected_files:
            return "(no affected files found)"
        parts = []
        for rel_path in affected_files:
            content = (project_dir / rel_path).read_text(encoding="utf-8")
            parts.append(f"### {rel_path}\n```python\n{content}\n```")
        return "\n\n".join(parts)

    def _build_replan_context(
        self,
        replan_feedback: dict[str, Any] | None,
        replan_attempt: int,
    ) -> str:
        if not replan_feedback:
            return ""
        return (
            f"## Replanning context (attempt {replan_attempt})\n\n"
            "A previous plan was rejected by the Validation Agent with this feedback:\n\n"
            f"{json.dumps(replan_feedback, indent=2, sort_keys=True)}\n\n"
            "Revise the migration plan to address this feedback."
        )

    def _normalize_steps(
        self,
        steps: list[PlannerV3Step],
        affected_source_files: list[str],
        dependency_files: list[str],
        dependency_summary: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        allowed_targets = set(affected_source_files) | set(dependency_files)
        normalized: list[dict[str, Any]] = []
        warnings: list[str] = []

        for step in steps:
            payload = step.model_dump()
            if payload["file"] not in allowed_targets:
                warnings.append(
                    f"Dropped step {payload['step_id']} for {payload['file']}: "
                    "file is not an affected production file or dependency file."
                )
                continue

            allowed_files = [
                file for file in payload.get("allowed_files", []) if file in allowed_targets
            ]
            if payload["file"] not in allowed_files:
                allowed_files.insert(0, payload["file"])

            payload["step_id"] = f"step_{len(normalized) + 1:03d}"
            payload["status"] = "planned"
            payload["allowed_files"] = allowed_files
            payload["allowed_symbols"] = payload.get("allowed_symbols", [])
            payload["files"] = payload.get("files", [])
            payload.setdefault("step_type", "single_file")
            normalized.append(payload)

        if (
            normalized
            and dependency_summary.get("target_dependency_action") == "add_dependency"
            and "requirements.txt" in dependency_files
            and "requirements.txt" not in normalized[0]["allowed_files"]
        ):
            normalized[0]["allowed_files"].append("requirements.txt")
            warnings.append(
                "Added requirements.txt to the first migration step because the "
                "target dependency is not present."
            )

        return normalized, warnings
