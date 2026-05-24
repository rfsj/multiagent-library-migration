from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.tools.project_scanner import scan_project

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
"""


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

class MigrationStep(BaseModel):
    step_id: str = Field(
        description="Unique step identifier in the format step_001, step_002, ..."
    )
    file: str = Field(
        description="File path relative to the repository root."
    )
    description: str = Field(
        description="Human-readable summary of the migration intent for this file."
    )
    allowed_files: list[str] = Field(
        description=(
            "Files the MigrationAgent is allowed to modify in this step. "
            "Typically the affected file only; add requirements.txt if a "
            "dependency change is needed."
        )
    )
    status: str = Field(
        default="planned",
        description="Always 'planned'.",
    )


class DiagnosisPlan(BaseModel):
    source_library: str = Field(description="Library being migrated from.")
    target_library: str = Field(description="Library being migrated to.")
    dependency_files: list[str] = Field(description="Dependency files found in the project.")
    affected_files: list[str] = Field(
        description="Source files that import or call the source library."
    )
    related_tests: list[str] = Field(
        description="Test files associated with the affected source files."
    )
    complexity: dict[str, str] = Field(
        description="Complexity per affected file. Values: 'low', 'medium', or 'high'."
    )
    migration_steps: list[MigrationStep] = Field(
        description="Ordered list of migration steps for the MigrationAgent."
    )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class DiagnosisAgent:
    """LangChain-powered agent that identifies migration scope and builds the plan."""

    name = "diagnosis_agent"

    def __init__(self, model: str | None = None) -> None:
        system_prompt = (_PROMPTS_DIR / "diagnosis_agent_v1.md").read_text(encoding="utf-8")

        llm = ChatAnthropic(
            model=model or os.getenv("DIAGNOSIS_MODEL", "claude-sonnet-4-6"),
        ).with_structured_output(DiagnosisPlan)

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
    ) -> dict[str, Any]:
        logs_dir.mkdir(parents=True, exist_ok=True)
        scan = scan_project(project_dir, source_library)

        result: DiagnosisPlan = self._chain.invoke({
            "source_library": source_library,
            "target_library": target_library,
            "file_contents": self._collect_file_contents(project_dir, scan["affected_files"]),
            "dependency_files": scan["dependency_files"],
            "test_files": scan["test_files"],
            "affected_files": scan["affected_files"],
        })

        plan = {
            "agent": self.name,
            "source_library": result.source_library,
            "target_library": result.target_library,
            "read_only": True,
            "dependency_files": result.dependency_files,
            "affected_files": result.affected_files,
            "related_tests": result.related_tests,
            "complexity": result.complexity,
            "migration_steps": [step.model_dump() for step in result.migration_steps],
        }

        (logs_dir / "diagnosis_plan.json").write_text(
            json.dumps(plan, indent=2), encoding="utf-8"
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
