from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.llm import get_llm

load_dotenv()

_PROMPTS_DIR = Path(__file__).parents[2] / "prompts"
MAX_STRUCTURED_OUTPUT_ATTEMPTS = 2

_HUMAN_TEMPLATE = """\
Review this proposed migration before validation.

## Planned step
{planned_step}

## DataFrame flow analysis
{dataframe_flow_analysis}

## Original code
```python
{original_code}
```

## Proposed migrated code
```python
{migrated_code}
```

Return a structured review. Do not rewrite the file. If revision is needed,
provide specific instructions that the MigrationAgent can use in a second pass.

Before choosing `approved`, verify each of these points internally:
- planned scope and allowed symbols are respected;
- no source-library usage remains inside the planned migrated scope;
- producers and consumers in the DataFrame flow keep compatible DataFrame types;
- Polars code does not create and reference a dependent column in the same
  `with_columns` call;
- expected selected columns, sort order, null handling, and return shape are
  preserved.
"""


class ImplementationIssue(BaseModel):
    kind: str = Field(description="Short issue category.")
    file: str = Field(description="Affected file.")
    symbol: str = Field(default="", description="Affected function/class if known.")
    explanation: str = Field(description="Why this is a migration risk.")


class ImplementationReviewResult(BaseModel):
    status: Literal["approved", "needs_revision"] = Field(
        description="Whether the proposed migration is ready for validation."
    )
    issues: list[ImplementationIssue] = Field(default_factory=list)
    revision_instructions: str = Field(
        default="",
        description="Concrete instructions for revising the migration.",
    )
    confidence: Literal["low", "medium", "high"] = "medium"


class ImplementationReviewAgent:
    """Reviews migrated code before validation without editing files."""

    name = "implementation_review_agent"

    def __init__(self) -> None:
        system_prompt = (_PROMPTS_DIR / "implementation_review_agent_v1.md").read_text(
            encoding="utf-8"
        )
        llm = get_llm().with_structured_output(ImplementationReviewResult)
        self._chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", system_prompt),
                    ("human", _HUMAN_TEMPLATE),
                ]
            )
            | llm
        )

    def review(
        self,
        *,
        rel_file: Path,
        original_code: str,
        migrated_code: str,
        planned_step: dict[str, Any],
        dataframe_flow_analysis: dict[str, Any],
        logs_dir: Path,
        log_suffix: str = "implementation_review",
    ) -> dict[str, Any]:
        result = self._invoke_structured_review(
            original_code=original_code,
            migrated_code=migrated_code,
            planned_step=planned_step,
            dataframe_flow_analysis=dataframe_flow_analysis,
        )
        if result is None:
            payload = self._fallback_review_payload(rel_file, planned_step)
        else:
            review, attempts = result
            payload = {
                "agent": self.name,
                "step_id": planned_step["step_id"],
                "file": str(rel_file),
                "structured_output_attempts": attempts,
                **review.model_dump(),
            }
        payload = self._normalize_review_payload(
            payload,
            rel_file=rel_file,
            original_code=original_code,
            migrated_code=migrated_code,
        )
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / f"{planned_step['step_id']}_{log_suffix}.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        return payload

    def _invoke_structured_review(
        self,
        *,
        original_code: str,
        migrated_code: str,
        planned_step: dict[str, Any],
        dataframe_flow_analysis: dict[str, Any],
    ) -> tuple[ImplementationReviewResult, int] | None:
        prompt_payload = {
            "planned_step": json.dumps(planned_step, indent=2, sort_keys=True),
            "dataframe_flow_analysis": json.dumps(
                dataframe_flow_analysis, indent=2, sort_keys=True
            ),
            "original_code": original_code,
            "migrated_code": migrated_code,
        }
        for attempt in range(1, MAX_STRUCTURED_OUTPUT_ATTEMPTS + 1):
            result = self._chain.invoke(prompt_payload)
            if result is not None:
                return result, attempt
        return None

    def _fallback_review_payload(
        self,
        rel_file: Path,
        planned_step: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "agent": self.name,
            "step_id": planned_step["step_id"],
            "file": str(rel_file),
            "structured_output_attempts": MAX_STRUCTURED_OUTPUT_ATTEMPTS,
            "structured_output_error": "Implementation review returned no structured output.",
            "status": "needs_revision",
            "issues": [
                {
                    "kind": "structured_output_missing",
                    "file": str(rel_file),
                    "symbol": "",
                    "explanation": (
                        "ImplementationReviewAgent could not produce a structured "
                        "review after retrying. Treat this as a review failure and "
                        "revise conservatively before validation."
                    ),
                }
            ],
            "revision_instructions": (
                "The implementation review did not return structured output. "
                "Retry the migration conservatively, keeping the planned scope, "
                "preserving behavior, and avoiding unsupported Polars APIs."
            ),
            "confidence": "low",
        }

    def _normalize_review_payload(
        self,
        payload: dict[str, Any],
        rel_file: Path | None = None,
        original_code: str | None = None,
        migrated_code: str | None = None,
    ) -> dict[str, Any]:
        if original_code is not None and migrated_code is not None:
            missing_symbols = _missing_top_level_symbols(original_code, migrated_code)
            if missing_symbols:
                payload = dict(payload)
                issues = list(payload.get("issues", []))
                for symbol in missing_symbols:
                    issues.append(
                        {
                            "kind": "public_api_symbol_removed",
                            "file": str(rel_file or payload.get("file", "")),
                            "symbol": symbol,
                            "explanation": (
                                f"Top-level symbol `{symbol}` exists in the original "
                                "file but is missing from the migrated code. This can "
                                "break tests or downstream imports."
                            ),
                        }
                    )
                payload["issues"] = issues
                instructions = payload.get("revision_instructions", "").strip()
                restore_instruction = (
                    "Restore every missing top-level function/class from the "
                    "original file and migrate its implementation instead of "
                    "deleting or renaming it. Missing symbols: "
                    + ", ".join(missing_symbols)
                    + "."
                )
                payload["revision_instructions"] = (
                    f"{instructions}\n\n{restore_instruction}".strip()
                )

        if payload.get("issues") and payload.get("status") == "approved":
            payload = dict(payload)
            payload["status"] = "needs_revision"
            instructions = payload.get("revision_instructions", "").strip()
            if not instructions:
                payload["revision_instructions"] = (
                    "Revise the migrated code to address every issue listed in "
                    "the implementation review before validation."
                )
        return payload


def _missing_top_level_symbols(original_code: str, migrated_code: str) -> list[str]:
    original_symbols = _top_level_public_symbols(original_code)
    migrated_symbols = _top_level_public_symbols(migrated_code)
    return sorted(original_symbols - migrated_symbols)


def _top_level_public_symbols(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                symbols.add(node.name)
    return symbols
