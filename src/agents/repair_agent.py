from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.llm import get_llm, is_llm_timeout_error, with_structured_output

load_dotenv()

_PROMPTS_DIR = Path(__file__).parents[2] / "prompts"

_HUMAN_TEMPLATE = """\
Build a repair plan for a failed migration retry.

## Planned step
{planned_step}

## Migration result
{migration_result}

## Validation evidence
{validation_evidence}

## Current migrated code
```python
{migrated_code}
```

Return a structured repair plan. Do not rewrite the file. Your goal is to give
the MigrationAgent precise instructions for the next retry.
"""


class RepairPlan(BaseModel):
    failure_category: str = Field(
        description=(
            "One of: polars_api_error, producer_consumer_type_mismatch, "
            "dependent_expression_order, unsupported_operation, "
            "semantic_equivalence_error, unknown."
        )
    )
    root_cause: str = Field(description="Primary reason the migration failed.")
    repair_strategy: str = Field(description="Short strategy identifier.")
    instructions_for_migration_agent: list[str] = Field(
        description="Concrete ordered instructions for the next MigrationAgent retry."
    )
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        description=(
            "Observable conditions that must be true in the next migrated code "
            "for this repair to be considered applied."
        ),
    )
    must_not_do: list[str] = Field(
        default_factory=list,
        description="Patterns the MigrationAgent must avoid in the retry.",
    )
    confidence: str = Field(default="medium", description="low, medium, or high.")
    scope_note: str = Field(
        default="",
        description="Which files/symbols this repair targets and why.",
    )
    downstream_context: str = Field(
        default="",
        description="Note about failures outside allowed_files that should not be fixed in this retry.",
    )


class RepairAgent:
    """Turns validation failures into actionable migration repair plans."""

    name = "repair_agent"

    def __init__(self) -> None:
        system_prompt = (_PROMPTS_DIR / "repair_agent_v2.md").read_text(
            encoding="utf-8"
        )
        llm = with_structured_output(get_llm(), RepairPlan)
        self._chain = (
            ChatPromptTemplate.from_messages(
                [
                    SystemMessage(content=system_prompt),
                    ("human", _HUMAN_TEMPLATE),
                ]
            )
            | llm
        )

    def build_repair_plan(
        self,
        *,
        project_dir: Path,
        planned_step: dict[str, Any],
        migration_result: dict[str, Any],
        validation_evidence: dict[str, Any],
        logs_dir: Path,
        attempt: int,
    ) -> dict[str, Any]:
        rel_file = Path(planned_step["file"])
        migrated_code = ""
        target = project_dir / rel_file
        if target.exists() and target.is_file():
            migrated_code = target.read_text(encoding="utf-8")

        try:
            result: RepairPlan | None = self._chain.invoke(
                {
                    "planned_step": json.dumps(planned_step, indent=2, sort_keys=True),
                    "migration_result": json.dumps(
                        migration_result, indent=2, sort_keys=True
                    ),
                    "validation_evidence": json.dumps(
                        validation_evidence, indent=2, sort_keys=True
                    ),
                    "migrated_code": migrated_code,
                }
            )
        except Exception as exc:
            if not is_llm_timeout_error(exc):
                raise
            repair_payload = _fallback_repair_payload(validation_evidence)
            repair_payload["root_cause"] = (
                repair_payload["root_cause"]
                + f" Timeout while building repair plan: {exc}"
            )
        else:
            repair_payload = (
                result.model_dump()
                if result is not None
                else _fallback_repair_payload(validation_evidence)
            )
        payload = {
            "agent": self.name,
            "step_id": planned_step["step_id"],
            "file": str(rel_file),
            "attempt": attempt,
            **repair_payload,
        }
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / f"{planned_step['step_id']}_repair_{attempt:02d}.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        return payload


def _fallback_repair_payload(validation_evidence: dict[str, Any]) -> dict[str, Any]:
    actionable_feedback = validation_evidence.get("actionable_feedback") or (
        "Review the pytest failure excerpt and revise the implementation inside "
        "the planned scope."
    )
    pytest_feedback = validation_evidence.get("pytest_feedback")
    instructions = [str(actionable_feedback)]
    if pytest_feedback:
        instructions.append(
            "Use the pytest failure excerpt to identify every semantic or API "
            "mismatch still present in the migrated code."
        )
    return {
        "failure_category": "unknown",
        "root_cause": (
            "The RepairAgent LLM did not return a structured repair plan; "
            "falling back to validation evidence."
        ),
        "repair_strategy": "fallback_to_validation_feedback",
        "instructions_for_migration_agent": instructions,
        "acceptance_criteria": [
            "The next migration retry addresses the validation feedback.",
            "The planned tests pass without out-of-scope changes.",
        ],
        "must_not_do": [
            "Do not edit files outside allowed_files.",
            "Do not change tests to make the migration pass.",
            "Do not introduce benchmark-specific hardcoded values.",
        ],
        "confidence": "low",
    }
