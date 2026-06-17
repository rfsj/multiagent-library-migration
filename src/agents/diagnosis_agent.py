from __future__ import annotations

import json
import ast
import os
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from langchain_core.messages import SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, ConfigDict, Field

from src.llm import (
    format_llm_timeout_error,
    get_llm,
    is_llm_timeout_error,
    with_structured_output,
)
from src.tools.project_scanner import build_project_audit, scan_project

load_dotenv()

_PROMPTS_DIR = Path(__file__).parents[2] / "prompts"


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


_HUMAN_TEMPLATE = """\
Analyze the project below and produce a migration plan from \
{source_library} to {target_library}.

## Source files provided for analysis

{file_contents}

## Structural metadata (from static analysis)

- Dependency files: {dependency_files}
- Dependency summary: {dependency_summary}
- Test files: {test_files}
- Affected or candidate production files: {affected_source_files}
- Test files that use {source_library} for fixtures/assertions: {test_files_with_source_library_usage}

## DataFrame flow analysis

{dataframe_flow}

{replan_context}
"""

_FLOW_HUMAN_TEMPLATE = """\
Analyze DataFrame flow before planning a migration from {source_library} to \
{target_library}.

Focus on functions/classes that create, return, receive, or transform DataFrame-like
objects. Identify producer/consumer relationships across files and mark groups that
must be migrated together or at file level to preserve type consistency.
Produce only the DataFrame flow analysis; do not produce migration steps in this
stage.

## Source files with {source_library} usage

{file_contents}

## Structural metadata

- Production files requiring migration: {affected_source_files}
- Dependency files: {dependency_files}
"""


# ---------------------------------------------------------------------------
# Output schema — v2
# ---------------------------------------------------------------------------


class ApiUsage(BaseModel):
    symbol: str = Field(default="")
    line: int = Field(default=0)
    api_call: str = Field(default="")
    has_polars_equivalent: bool = Field(default=True)


class AffectedFile(BaseModel):
    file: str = Field(description="Relative path of the affected production file.")
    complexity: str = Field(default="low", description="low | medium | high")
    risk_level: str = Field(default="low", description="low | medium | high")
    risk_factors: list[str] = Field(default_factory=list)
    imports: list[dict] = Field(default_factory=list)
    api_usages: list[ApiUsage] = Field(default_factory=list)


class TestFile(BaseModel):
    file: str = Field(default="")
    detection_method: str = Field(default="")
    uses_source_library: bool = Field(default=False)
    related_production_symbols: list[str] = Field(default_factory=list)


class ApiMapping(BaseModel):
    from_api: str = Field(default="", alias="from")
    to_api: str = Field(default="", alias="to")
    confidence: str = Field(default="high")

    model_config = ConfigDict(populate_by_name=True)


class AmbiguousApi(BaseModel):
    api_call: str = Field(default="")
    line: int = Field(default=0)
    reason: str = Field(default="")


class StepDataFrameFlow(BaseModel):
    producers: list[str] = Field(default_factory=list)
    consumers: list[str] = Field(default_factory=list)
    coupled_with: list[str] = Field(default_factory=list)


class MigrationStep(BaseModel):
    step_id: str = Field(description="Unique step identifier: step_001, step_002, ...")
    status: str = Field(default="planned")
    step_type: str = Field(
        default="single_file", description="single_symbol | single_file | grouped"
    )
    file: str = Field(description="Primary file path relative to the repository root.")
    files: list[str] = Field(
        default_factory=list, description="All files in this step."
    )
    description: str = Field(
        description="Human-readable summary of the migration intent."
    )
    allowed_files: list[str] = Field(description="Files the MigrationAgent may modify.")
    allowed_symbols: list[str] = Field(default_factory=list)
    complexity: str = Field(default="low", description="low | medium | high")
    risk_level: str = Field(default="low", description="low | medium | high")
    risk_factors: list[str] = Field(default_factory=list)
    requires_human_review: bool = Field(default=False)
    human_review_reasons: list[str] = Field(default_factory=list)
    dataframe_flow_analysis: Optional[StepDataFrameFlow] = Field(default=None)
    api_mappings_needed: list[ApiMapping] = Field(default_factory=list)
    ambiguous_apis: list[AmbiguousApi] = Field(default_factory=list)
    upstream_dependencies: list[str] = Field(default_factory=list)
    upstream_failed_files: list[str] = Field(default_factory=list)
    related_tests: list[str] = Field(default_factory=list)
    validation_commands: list[str] = Field(default_factory=list)


class DataFrameFlowEntry(BaseModel):
    producer_file: str = Field(default="")
    producer_symbol: str = Field(default="")
    consumer_file: str = Field(default="")
    consumer_symbol: str = Field(default="")
    confidence: str = Field(default="medium")
    evidence: str = Field(default="")


class CoupledGroup(BaseModel):
    group_id: str = Field(default="")
    files: list[str] = Field(default_factory=list)
    reason: str = Field(default="")
    confidence: str = Field(default="medium")


class DependencyAnalysis(BaseModel):
    source_library_present: bool = Field(default=True)
    target_library_present: bool = Field(default=False)
    dependency_files_found: list[str] = Field(default_factory=list)
    dependency_update_required: bool = Field(default=False)
    dependency_file_to_update: Optional[str] = Field(default=None)
    notes: str = Field(default="")


class ResearchMetricsSupport(BaseModel):
    predicted_files_changed: list[str] = Field(default_factory=list)
    predicted_symbols_changed: list[str] = Field(default_factory=list)
    ambiguous_api_count: int = Field(default=0)
    unmigratable_api_count: int = Field(default=0)


class DiagnosisPlan(BaseModel):
    source_library: str = Field(description="Library being migrated from.")
    target_library: str = Field(description="Library being migrated to.")
    # v1 compat fields (may be empty when v2 detail fields are present)
    dependency_files: list[str] = Field(
        default_factory=list,
        description="Flat list of dependency file paths found (for backward compat).",
    )
    affected_files: list[AffectedFile] = Field(
        default_factory=list,
        description="Production files that import or call the source library.",
    )
    related_tests: list[str] = Field(
        default_factory=list,
        description="Test file paths associated with affected source files.",
    )
    complexity: dict = Field(
        default_factory=dict,
        description="Complexity per affected file (legacy). Prefer per-step complexity.",
    )
    migration_steps: list[MigrationStep] = Field(
        description="Ordered list of migration steps for the MigrationAgent."
    )
    # v2 additions
    dependency_analysis: Optional[DependencyAnalysis] = Field(default=None)
    test_files: list[TestFile] = Field(default_factory=list)
    dataframe_flow: list[DataFrameFlowEntry] = Field(default_factory=list)
    coupled_groups: list[CoupledGroup] = Field(default_factory=list)
    human_review_required: bool = Field(default=False)
    human_review_reasons: list[str] = Field(default_factory=list)
    research_metrics_support: Optional[ResearchMetricsSupport] = Field(default=None)
    assumptions: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Flow analysis schema (separate LLM call)
# ---------------------------------------------------------------------------


class DataFrameFlowSymbol(BaseModel):
    file: str = Field(description="File path relative to the repository root.")
    symbol: str = Field(description="Function or class name.")
    role: str = Field(
        description=(
            "Flow role such as producer, consumer, transformer, mixed, or unknown."
        )
    )
    returns_dataframe: bool = Field(
        default=False,
        description="Whether this symbol appears to return a DataFrame-like object.",
    )
    consumes_dataframe_from: list[str] = Field(
        default_factory=list,
        description="Symbols this symbol depends on for DataFrame-like inputs.",
    )
    type_contract: str = Field(
        default="unknown",
        description="Expected DataFrame type contract before migration.",
    )


class DataFrameFlowGroup(BaseModel):
    group_id: str = Field(description="Stable identifier such as flow_group_001.")
    files: list[str] = Field(
        description="Files that should be planned as a coupled migration group."
    )
    symbols: list[str] = Field(
        default_factory=list,
        description="Symbols involved in the coupled DataFrame flow.",
    )
    reason: str = Field(description="Why this flow is coupled.")
    planning_strategy: str = Field(
        description=(
            "Recommended planning strategy, for example file_level_steps or "
            "grouped_before_consumers."
        )
    )


class DataFrameFlowAnalysis(BaseModel):
    symbols: list[DataFrameFlowSymbol] = Field(default_factory=list)
    groups: list[DataFrameFlowGroup] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class DiagnosisAgent:
    """LangChain-powered agent that identifies migration scope and builds the plan."""

    name = "diagnosis_agent"

    def __init__(self) -> None:
        system_prompt = (_PROMPTS_DIR / "diagnosis_agent_v2.md").read_text(
            encoding="utf-8"
        )

        raw_llm = get_llm()
        flow_llm = with_structured_output(raw_llm, DataFrameFlowAnalysis)

        prompt = ChatPromptTemplate.from_messages(
            [
                SystemMessage(content=system_prompt),
                ("human", _HUMAN_TEMPLATE),
            ]
        )
        # Use StrOutputParser so we always get the raw text and parse manually.
        # with_structured_output returns None for complex schemas when the model
        # generates JSON text instead of a function call.
        self._chain = prompt | raw_llm | StrOutputParser()

        flow_prompt = ChatPromptTemplate.from_messages(
            [
                SystemMessage(content=system_prompt),
                ("human", _FLOW_HUMAN_TEMPLATE),
            ]
        )
        self._flow_chain = flow_prompt | flow_llm
        self._raw_flow_chain = flow_prompt | raw_llm | StrOutputParser()
        self._use_ast = _env_flag("DIAGNOSIS_USE_AST", True)

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
        scan = scan_project(project_dir, source_library, use_ast=self._use_ast)
        audit = build_project_audit(
            project_dir,
            source_library,
            target_library,
            use_ast=self._use_ast,
        )
        audit_log_name = (
            "project_audit.json"
            if replan_attempt == 0
            else f"project_audit_replan_{replan_attempt}.json"
        )
        (logs_dir / audit_log_name).write_text(
            json.dumps(audit, indent=2), encoding="utf-8"
        )

        source_scope_files = (
            scan["affected_source_files"]
            if self._use_ast
            else scan["production_source_files"]
        )
        file_contents = self._collect_file_contents(project_dir, source_scope_files)
        flow_payload = {
            "source_library": source_library,
            "target_library": target_library,
            "file_contents": file_contents,
            "dependency_files": scan["dependency_files"],
            "affected_source_files": source_scope_files,
        }
        try:
            dataframe_flow_result = self._flow_chain.invoke(flow_payload)
            dataframe_flow: Optional[DataFrameFlowAnalysis] = (
                dataframe_flow_result
                if isinstance(dataframe_flow_result, DataFrameFlowAnalysis)
                else None
            )
        except Exception as exc:
            if not is_llm_timeout_error(exc):
                raise
            dataframe_flow = None
        if dataframe_flow is None:
            # Fallback: parse raw text if function calling returned nothing
            try:
                raw_text: str = self._raw_flow_chain.invoke(flow_payload)
            except Exception as exc:
                if not is_llm_timeout_error(exc):
                    raise
                dataframe_flow = DataFrameFlowAnalysis()
            else:
                try:
                    raw_text = raw_text.strip()
                    if raw_text.startswith("```"):
                        lines = raw_text.splitlines()
                        raw_text = "\n".join(
                            line for line in lines if not line.startswith("```")
                        ).strip()
                    dataframe_flow = DataFrameFlowAnalysis.model_validate(
                        json.loads(raw_text)
                    )
                except Exception:
                    dataframe_flow = DataFrameFlowAnalysis()
        flow_log_name = (
            "dataframe_flow_analysis.json"
            if replan_attempt == 0
            else f"dataframe_flow_analysis_replan_{replan_attempt}.json"
        )
        dataframe_flow_payload = dataframe_flow.model_dump()
        (logs_dir / flow_log_name).write_text(
            json.dumps(dataframe_flow_payload, indent=2), encoding="utf-8"
        )

        result = self._invoke_plan_with_retry(
            logs_dir,
            replan_attempt,
            {
                "source_library": source_library,
                "target_library": target_library,
                "file_contents": file_contents,
                "dependency_files": scan["dependency_files"],
                "dependency_summary": json.dumps(
                    audit["dependency_summary"], indent=2, sort_keys=True
                ),
                "test_files": scan["test_files"],
                "affected_source_files": source_scope_files,
                "test_files_with_source_library_usage": scan[
                    "test_files_with_source_library_usage"
                ],
                "dataframe_flow": json.dumps(
                    dataframe_flow_payload, indent=2, sort_keys=True
                ),
                "replan_context": self._build_replan_context(
                    replan_feedback, replan_attempt
                ),
            },
        )

        # Derive flat affected_files list for downstream compat (v2 returns objects)
        affected_files_flat = [
            af.file if hasattr(af, "file") else str(af)
            for af in (result.affected_files or [])
        ]
        affected_source_files = _planned_source_files_legacy(
            result.migration_steps,
            affected_files_flat,
            source_scope_files,
            use_ast=self._use_ast,
        )
        migration_steps, planner_warnings = self._sanitize_migration_steps(
            result.migration_steps,
            affected_source_files,
            scan["dependency_files"],
            audit["dependency_summary"],
            project_dir,
            source_library,
            dataframe_flow_payload,
        )

        # Derive flat dependency_files list from v2 dependency_analysis when present
        dep_files = result.dependency_files or []
        if not dep_files and result.dependency_analysis:
            dep_files = result.dependency_analysis.dependency_files_found or []

        plan = {
            "agent": self.name,
            "source_library": result.source_library,
            "target_library": result.target_library,
            "read_only": True,
            "diagnosis_use_ast": self._use_ast,
            "dependency_files": dep_files,
            "dependency_summary": audit["dependency_summary"],
            "affected_files": affected_files_flat,
            "affected_files_detail": [
                af.model_dump() if hasattr(af, "model_dump") else af
                for af in (result.affected_files or [])
            ],
            "affected_source_files": affected_source_files,
            "test_files_with_source_library_usage": scan[
                "test_files_with_source_library_usage"
            ],
            "related_tests": result.related_tests,
            "complexity": result.complexity,
            "dataframe_flow_analysis": dataframe_flow_payload,
            "planner_warnings": planner_warnings,
            "migration_steps": migration_steps,
            # v2 additions
            "human_review_required": result.human_review_required,
            "human_review_reasons": result.human_review_reasons,
            "assumptions": result.assumptions,
            "unknowns": result.unknowns,
        }
        if result.research_metrics_support:
            plan["research_metrics_support"] = (
                result.research_metrics_support.model_dump()
            )

        log_name = (
            "diagnosis_plan.json"
            if replan_attempt == 0
            else f"diagnosis_plan_replan_{replan_attempt}.json"
        )
        (logs_dir / log_name).write_text(json.dumps(plan, indent=2), encoding="utf-8")
        return plan

    def _collect_file_contents(
        self, project_dir: Path, affected_files: list[str]
    ) -> str:
        if not affected_files:
            return "(no affected files found)"
        parts = []
        for rel_path in affected_files:
            content = (project_dir / rel_path).read_text(encoding="utf-8")
            parts.append(f"### {rel_path}\n```python\n{content}\n```")
        return "\n\n".join(parts)

    def _invoke_plan_with_retry(
        self,
        logs_dir: Path,
        replan_attempt: int,
        payload: dict[str, Any],
    ) -> DiagnosisPlan:
        attempts = []
        for attempt in range(1, 3):
            try:
                text: str = self._chain.invoke(payload)
            except Exception as exc:
                if not is_llm_timeout_error(exc):
                    raise
                attempts.append(
                    {
                        "attempt": attempt,
                        "status": "timeout",
                        "action": "retry" if attempt == 1 else "fail",
                        "error": format_llm_timeout_error(
                            f"diagnosis plan generation attempt {attempt}", exc
                        ),
                    }
                )
                if attempt == 2:
                    self._write_planner_retry_log(logs_dir, replan_attempt, attempts)
                    raise RuntimeError(attempts[-1]["error"]) from exc
                continue
            result = self._parse_plan_text(text)
            if result is not None:
                if attempts:
                    attempts.append({"attempt": attempt, "status": "success"})
                    self._write_planner_retry_log(logs_dir, replan_attempt, attempts)
                return result
            attempts.append(
                {
                    "attempt": attempt,
                    "status": "json_parse_failed",
                    "action": "retry" if attempt == 1 else "fail",
                    "text_preview": text[:200] if text else "",
                }
            )
            payload = {
                **payload,
                "replan_context": (
                    f"{payload.get('replan_context', '')}\n\n"
                    "The previous response could not be parsed as a valid migration plan. "
                    "Ensure every migration_steps entry includes the 'file' field. "
                    "Return a complete, valid DiagnosisPlan with migration_steps."
                ).strip(),
            }
        self._write_planner_retry_log(logs_dir, replan_attempt, attempts)
        raise RuntimeError(
            "DiagnosisAgent could not obtain a structured migration plan after retry."
        )

    def _parse_plan_text(self, text: str) -> Optional[DiagnosisPlan]:
        """Parse raw LLM text into a DiagnosisPlan, stripping markdown fences."""
        if not text:
            return None
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                line for line in lines if not line.startswith("```")
            ).strip()
        try:
            data = json.loads(text)
            return DiagnosisPlan.model_validate(data)
        except Exception:
            return None

    def _write_planner_retry_log(
        self,
        logs_dir: Path,
        replan_attempt: int,
        attempts: list[dict[str, Any]],
    ) -> None:
        suffix = "" if replan_attempt == 0 else f"_replan_{replan_attempt}"
        (logs_dir / f"diagnosis_plan_retry{suffix}.json").write_text(
            json.dumps(
                {
                    "agent": self.name,
                    "event": "structured_plan_retry",
                    "attempts": attempts,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def _sanitize_migration_steps(
        self,
        steps: list[MigrationStep],
        affected_source_files: list[str],
        dependency_files: list[str],
        dependency_summary: dict[str, Any],
        project_dir: Path,
        source_library: str,
        dataframe_flow: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        allowed_targets = set(affected_source_files) | set(dependency_files)
        allowed_scope = allowed_targets
        sanitized = []
        warnings = []
        use_ast = getattr(self, "_use_ast", True)
        for step in steps:
            payload = step.model_dump()
            if payload["file"] not in allowed_targets:
                warnings.append(
                    f"Dropped step {payload['step_id']} for {payload['file']}: "
                    "file is not a production affected file or dependency file."
                )
                continue
            original_allowed = list(payload.get("allowed_files", []))
            payload["allowed_files"] = [
                file for file in original_allowed if file in allowed_scope
            ]
            if payload["file"] not in payload["allowed_files"]:
                payload["allowed_files"].insert(0, payload["file"])
            removed = sorted(set(original_allowed) - set(payload["allowed_files"]))
            if removed:
                warnings.append(
                    f"Sanitized step {payload['step_id']} allowed_files; removed {removed}."
                )
            if payload["file"].endswith(".py"):
                if use_ast:
                    payload["allowed_symbols"] = _sanitize_allowed_symbols(
                        project_dir / payload["file"],
                        payload.get("allowed_symbols", []),
                        warnings,
                        payload["step_id"],
                        payload["file"],
                    )
                elif payload.get("allowed_symbols"):
                    warnings.append(
                        "DIAGNOSIS_USE_AST=0: kept allowed_symbols without "
                        "AST-based top-level symbol validation for "
                        f"{payload['file']}."
                    )
            sanitized.append(payload)
        sanitized = _deduplicate_migration_steps(sanitized, warnings)
        sanitized = _group_cross_file_flow_steps(
            sanitized,
            dataframe_flow or {},
            dependency_files,
            warnings,
        )
        sanitized = self._split_file_steps_by_symbol(
            project_dir,
            sanitized,
            source_library,
            dependency_summary,
            dependency_files,
            warnings,
            dataframe_flow or {},
            use_ast,
        )
        if (
            sanitized
            and dependency_summary.get("target_dependency_action") == "add_dependency"
            and "requirements.txt" in dependency_files
            and "requirements.txt" not in sanitized[0]["allowed_files"]
        ):
            sanitized[0]["allowed_files"].append("requirements.txt")
            warnings.append(
                "Added requirements.txt to the first migration step because the target "
                "dependency is not present and the step may introduce target-library imports."
            )
        return sanitized, warnings

    def _split_file_steps_by_symbol(
        self,
        project_dir: Path,
        steps: list[dict[str, Any]],
        source_library: str,
        dependency_summary: dict[str, Any],
        dependency_files: list[str],
        warnings: list[str],
        dataframe_flow: dict[str, Any],
        use_ast: bool,
    ) -> list[dict[str, Any]]:
        split_steps: list[dict[str, Any]] = []
        next_index = 1
        file_level_flow_files = _file_level_flow_files(dataframe_flow)
        for step in steps:
            if step.get("files"):
                cloned = dict(step)
                cloned["step_id"] = f"step_{next_index:03d}"
                cloned["allowed_symbols"] = []
                split_steps.append(cloned)
                next_index += 1
                continue

            rel_file = step["file"]
            if rel_file in file_level_flow_files:
                cloned = dict(step)
                cloned["step_id"] = f"step_{next_index:03d}"
                cloned["allowed_symbols"] = []
                split_steps.append(cloned)
                next_index += 1
                warnings.append(
                    f"Kept {rel_file} as a file-level step because DataFrame "
                    "flow analysis marked it as coupled with other migration targets."
                )
                continue

            if not rel_file.endswith(".py") or step.get("allowed_symbols"):
                cloned = dict(step)
                cloned["step_id"] = f"step_{next_index:03d}"
                split_steps.append(cloned)
                next_index += 1
                continue

            if not use_ast:
                cloned = dict(step)
                cloned["step_id"] = f"step_{next_index:03d}"
                split_steps.append(cloned)
                next_index += 1
                warnings.append(
                    "DIAGNOSIS_USE_AST=0: skipped AST-based symbol splitting "
                    f"for {rel_file}."
                )
                continue

            symbols = _migratable_symbols(project_dir / rel_file, source_library)
            if len(symbols) <= 1:
                cloned = dict(step)
                cloned["step_id"] = f"step_{next_index:03d}"
                split_steps.append(cloned)
                next_index += 1
                continue

            call_graph = _symbol_call_graph(project_dir / rel_file, source_library)
            ordered = _topological_symbol_order(symbols, call_graph)
            if ordered is not None:
                warnings.append(
                    f"Split {rel_file} into {len(ordered)} symbol-level steps "
                    "in producer-consumer order (intra-file dependency detected)."
                )
            else:
                ordered = symbols
                warnings.append(
                    f"Split {rel_file} into {len(symbols)} symbol-level migration steps."
                )
            for symbol in ordered:
                cloned = dict(step)
                cloned["step_id"] = f"step_{next_index:03d}"
                cloned["description"] = f"Migrate {symbol} in {rel_file}."
                cloned["allowed_symbols"] = [symbol]
                split_steps.append(cloned)
                next_index += 1

        if (
            split_steps
            and dependency_summary.get("target_dependency_action") == "add_dependency"
            and "requirements.txt" in dependency_files
            and "requirements.txt" not in split_steps[0]["allowed_files"]
        ):
            split_steps[0]["allowed_files"].append("requirements.txt")
        return split_steps

    def _build_replan_context(
        self, replan_feedback: dict[str, Any] | None, replan_attempt: int
    ) -> str:
        if not replan_feedback:
            return ""
        return (
            f"## Replanning context (attempt {replan_attempt})\n\n"
            "A previous plan was rejected by the Validation Agent with the following feedback:\n\n"
            f"{json.dumps(replan_feedback, indent=2, sort_keys=True)}\n\n"
            "Revise the migration plan to address this feedback."
        )


_DATAFRAME_METHODS = {
    "agg",
    "apply",
    "astype",
    "copy",
    "drop_duplicates",
    "dt",
    "fillna",
    "groupby",
    "isna",
    "isin",
    "merge",
    "pivot_table",
    "reset_index",
    "round",
    "sort_values",
    "to_dict",
}


def _migratable_symbols(path: Path, source_library: str) -> list[str]:
    if not path.exists():
        return []
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    aliases = _library_aliases(tree, source_library)

    symbols = []
    for node in tree.body:
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and _symbol_uses_dataframe_api(node, aliases):
            symbols.append(node.name)
    return symbols


def _planned_source_files_legacy(
    steps: list[MigrationStep],
    affected_files: list[str],
    candidate_files: list[str],
    *,
    use_ast: bool,
) -> list[str]:
    if use_ast:
        return candidate_files

    candidates = set(candidate_files)
    selected = {rel_file for rel_file in affected_files if rel_file in candidates}
    for step in steps:
        payload = step.model_dump() if hasattr(step, "model_dump") else dict(step)
        file = payload.get("file")
        if file in candidates:
            selected.add(file)
        selected.update(
            rel_file
            for rel_file in (payload.get("files", []) or [])
            if rel_file in candidates
        )
        selected.update(
            rel_file
            for rel_file in (payload.get("allowed_files", []) or [])
            if rel_file in candidates
        )
    return [rel_file for rel_file in candidate_files if rel_file in selected]


def _sanitize_allowed_symbols(
    path: Path,
    symbols: list[str],
    warnings: list[str],
    step_id: str,
    rel_file: str,
) -> list[str]:
    if not symbols:
        return []
    valid_symbols = _top_level_symbol_names(path)
    sanitized = [symbol for symbol in symbols if symbol in valid_symbols]
    removed = sorted(set(symbols) - set(sanitized))
    if removed:
        warnings.append(
            f"Sanitized step {step_id} allowed_symbols for {rel_file}; "
            f"removed non-top-level symbols {removed}."
        )
    return sanitized


def _deduplicate_migration_steps(
    steps: list[dict[str, Any]],
    warnings: list[str],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    ordered_files = []
    for step in steps:
        rel_file = step["file"]
        if rel_file not in grouped:
            grouped[rel_file] = []
            ordered_files.append(rel_file)
        grouped[rel_file].append(step)

    deduplicated: list[dict[str, Any]] = []
    for rel_file in ordered_files:
        file_steps = grouped[rel_file]
        file_level_steps = [
            step for step in file_steps if not step.get("allowed_symbols")
        ]
        if file_level_steps:
            kept = _merge_step_group(file_level_steps, file_steps[0])
            deduplicated.append(kept)
            removed_count = len(file_steps) - 1
            if removed_count:
                warnings.append(
                    f"Deduplicated {removed_count} redundant migration step(s) "
                    f"for {rel_file}; kept one file-level step."
                )
            continue

        seen_keys: set[tuple[str, ...]] = set()
        for step in file_steps:
            key = tuple(step.get("allowed_symbols", []))
            if key in seen_keys:
                warnings.append(
                    f"Dropped duplicate migration step for {rel_file} "
                    f"with allowed_symbols {list(key)}."
                )
                continue
            seen_keys.add(key)
            deduplicated.append(step)
    return deduplicated


def _merge_step_group(
    file_level_steps: list[dict[str, Any]],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(file_level_steps[0] if file_level_steps else fallback)
    allowed_files: list[str] = []
    for step in file_level_steps or [fallback]:
        for rel_path in step.get("allowed_files", []):
            if rel_path not in allowed_files:
                allowed_files.append(rel_path)
    merged["allowed_files"] = allowed_files
    merged["allowed_symbols"] = []
    return merged


def _group_cross_file_flow_steps(
    steps: list[dict[str, Any]],
    dataframe_flow: dict[str, Any],
    dependency_files: list[str],
    warnings: list[str],
) -> list[dict[str, Any]]:
    grouped_files = _grouped_flow_files(dataframe_flow)
    if not grouped_files:
        return steps

    step_by_file = {step["file"]: step for step in steps}
    used_files: set[str] = set()
    grouped_steps: list[dict[str, Any]] = []

    for files, reason in grouped_files:
        present_files = [file for file in files if file in step_by_file]
        if len(present_files) <= 1:
            continue

        ordered = _file_dependency_order(present_files, dataframe_flow)
        primary = dict(step_by_file[ordered[0]])
        allowed_files: list[str] = []
        for rel_file in ordered:
            for af in step_by_file[rel_file].get("allowed_files", []):
                if af not in allowed_files:
                    allowed_files.append(af)
            if rel_file not in allowed_files:
                allowed_files.append(rel_file)
        for dep in dependency_files:
            if dep not in allowed_files:
                allowed_files.append(dep)
        primary["file"] = ordered[0]
        primary["files"] = ordered
        primary["allowed_files"] = allowed_files
        primary["allowed_symbols"] = []
        primary["description"] = (
            f"Migrate coupled DataFrame flow ({len(ordered)} files atomically). "
            f"Reason: {reason}"
        )
        grouped_steps.append(primary)
        used_files.update(ordered)
        warnings.append(
            "Grouped DataFrame flow files into one atomic migration step: "
            + ", ".join(ordered)
        )

    if not grouped_steps:
        return steps

    result: list[dict[str, Any]] = []
    for step in steps:
        if step["file"] not in used_files:
            result.append(step)
    return grouped_steps + result


def _file_dependency_order(
    files: list[str],
    dataframe_flow: dict[str, Any],
) -> list[str]:
    """Return *files* in topological order by cross-file producer-consumer links.

    Reads ``consumes_dataframe_from`` edges from the DataFrameFlowAnalysis
    symbols to discover which files must be migrated before others.  Falls back
    to the original order on cycles or when no cross-file edges exist.
    """
    from collections import deque

    file_set = set(files)
    symbols = dataframe_flow.get("symbols", [])

    symbol_to_file: dict[str, str] = {
        s["symbol"]: s["file"]
        for s in symbols
        if s.get("symbol") and s.get("file") in file_set
    }

    # file_deps[consumer_file] = {producer_files it depends on}
    file_deps: dict[str, set[str]] = {f: set() for f in files}
    for sym in symbols:
        consumer_file = sym.get("file")
        if consumer_file not in file_set:
            continue
        for producer_sym in sym.get("consumes_dataframe_from", []):
            producer_file = symbol_to_file.get(producer_sym)
            if producer_file and producer_file != consumer_file:
                file_deps[consumer_file].add(producer_file)

    if not any(file_deps.values()):
        return files

    in_degree = {f: len(file_deps[f]) for f in files}
    reverse: dict[str, set[str]] = {f: set() for f in files}
    for consumer, producers in file_deps.items():
        for producer in producers:
            if producer in reverse:
                reverse[producer].add(consumer)

    queue: deque[str] = deque(f for f in files if in_degree[f] == 0)
    result: list[str] = []
    while queue:
        f = queue.popleft()
        result.append(f)
        for consumer in sorted(reverse.get(f, set())):
            in_degree[consumer] -= 1
            if in_degree[consumer] == 0:
                queue.append(consumer)

    return result if len(result) == len(files) else files


def _grouped_flow_files(dataframe_flow: dict[str, Any]) -> list[tuple[list[str], str]]:
    groups: list[tuple[list[str], str]] = []
    for group in dataframe_flow.get("groups", []):
        if group.get("planning_strategy") != "grouped_before_consumers":
            continue
        files = [
            file
            for file in group.get("files", [])
            if isinstance(file, str) and file.endswith(".py")
        ]
        unique_files = list(dict.fromkeys(files))
        if len(unique_files) > 1:
            groups.append((unique_files, str(group.get("reason", ""))))
    return groups


def _file_level_flow_files(dataframe_flow: dict[str, Any]) -> set[str]:
    files: set[str] = set()
    for group in dataframe_flow.get("groups", []):
        strategy = group.get("planning_strategy", "")
        group_files = [file for file in group.get("files", []) if file.endswith(".py")]
        if len(group_files) <= 1:
            continue
        if strategy in {"file_level_steps", "grouped_before_consumers"}:
            files.update(group_files)
    files.update(_cross_file_dataframe_flow_files(dataframe_flow))
    return files


def _cross_file_dataframe_flow_files(dataframe_flow: dict[str, Any]) -> set[str]:
    symbols = dataframe_flow.get("symbols", [])
    symbol_to_file = {
        symbol.get("symbol"): symbol.get("file")
        for symbol in symbols
        if symbol.get("symbol") and symbol.get("file")
    }
    coupled_files: set[str] = set()
    for symbol in symbols:
        consumer_file = symbol.get("file")
        if not consumer_file:
            continue
        for producer_symbol in symbol.get("consumes_dataframe_from", []):
            producer_file = symbol_to_file.get(producer_symbol)
            if not producer_file or producer_file == consumer_file:
                continue
            coupled_files.update({producer_file, consumer_file})
    return {file for file in coupled_files if file.endswith(".py")}


def _top_level_symbol_names(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _symbol_uses_dataframe_api(node: ast.AST, aliases: set[str]) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute):
            if isinstance(child.value, ast.Name) and child.value.id in aliases:
                return True
            if child.attr in _DATAFRAME_METHODS:
                return True
        if isinstance(child, ast.Subscript):
            return True
    return False


def _library_aliases(tree: ast.AST, source_library: str) -> set[str]:
    aliases = {source_library}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == source_library:
                    aliases.add(alias.asname or source_library)
    return aliases


def _symbol_call_graph(path: Path, source_library: str) -> dict[str, set[str]]:
    """Return {caller: {callees}} among migratable symbols in *path*.

    Only edges between symbols that actually use the source-library API are
    included, so the graph captures producer-consumer relationships rather than
    every helper call.
    """
    if not path.exists():
        return {}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return {}

    aliases = _library_aliases(tree, source_library)
    top_level = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    migratable = {
        name
        for name, node in top_level.items()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _symbol_uses_dataframe_api(node, aliases)
    }
    return {
        name: {
            callee
            for callee in migratable
            if callee != name and _calls_local_symbol(top_level[name], {callee})
        }
        for name in migratable
    }


def _topological_symbol_order(
    symbols: list[str],
    call_graph: dict[str, set[str]],
) -> list[str] | None:
    """Kahn's topological sort on the intra-file call graph.

    Returns symbols ordered so producers come before consumers, or *None* when
    no intra-file dependency edges exist (nothing to order).
    """
    symbol_set = set(symbols)
    edges = {
        sym: {dep for dep in call_graph.get(sym, set()) if dep in symbol_set}
        for sym in symbols
    }
    if not any(edges.values()):
        return None

    in_degree = {sym: len(edges[sym]) for sym in symbols}
    reverse: dict[str, set[str]] = {sym: set() for sym in symbols}
    for sym, deps in edges.items():
        for dep in deps:
            reverse[dep].add(sym)

    from collections import deque

    queue: deque[str] = deque(sym for sym in symbols if in_degree[sym] == 0)
    result: list[str] = []
    while queue:
        sym = queue.popleft()
        result.append(sym)
        for consumer in sorted(reverse[sym]):
            in_degree[consumer] -= 1
            if in_degree[consumer] == 0:
                queue.append(consumer)

    if len(result) != len(symbols):
        return None  # cycle — fall back to unordered
    return result


def _should_keep_file_level_step(path: Path, source_library: str = "pandas") -> bool:
    if not path.exists():
        return False
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False

    aliases = _library_aliases(tree, source_library)
    top_level_symbols = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    dataframe_symbols = {
        name
        for name, node in top_level_symbols.items()
        if _symbol_uses_dataframe_api(node, aliases)
    }
    if len(dataframe_symbols) <= 1:
        return False

    for name in dataframe_symbols:
        if _calls_local_symbol(top_level_symbols[name], dataframe_symbols - {name}):
            return True
    return any(
        isinstance(node, ast.ClassDef)
        and _class_has_multiple_dataframe_methods(node, aliases)
        for node in top_level_symbols.values()
    )


def _calls_local_symbol(node: ast.AST, symbol_names: set[str]) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name) and child.func.id in symbol_names:
                return True
            if (
                isinstance(child.func, ast.Attribute)
                and child.func.attr in symbol_names
            ):
                return True
    return False


def _class_has_multiple_dataframe_methods(
    node: ast.ClassDef, aliases: set[str]
) -> bool:
    dataframe_methods = [
        child
        for child in node.body
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _symbol_uses_dataframe_api(child, aliases)
    ]
    if len(dataframe_methods) <= 1:
        return False
    method_names = {method.name for method in dataframe_methods}
    return any(
        _calls_local_symbol(method, method_names - {method.name})
        for method in dataframe_methods
    )
