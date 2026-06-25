from __future__ import annotations

import ast
import json
import os
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.llm import get_llm, with_structured_output
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
- Test files: {test_files}
- Affected or candidate production files: {affected_files}

## Structured symbol analysis

{symbol_analysis}

{replan_context}
"""

_SYMBOL_ANALYSIS_TEMPLATE = """\
Analyze the Python source files below before migration planning.

## Source library

{source_library}

## Target library

{target_library}

## Source files

{file_contents}
"""


@dataclass
class PlannerGuardrailEvent:
    rule: str
    severity: str
    action: str
    message: str
    step_id: str | None = None
    file: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "rule": self.rule,
            "severity": self.severity,
            "action": self.action,
            "message": self.message,
        }
        if self.step_id:
            payload["step_id"] = self.step_id
        if self.file:
            payload["file"] = self.file
        if self.details:
            payload["details"] = self.details
        return payload


class PlannerV3SymbolAnalysis(BaseModel):
    name: str = Field(description="Top-level function or class name.")
    kind: str = Field(description="function, async_function, class, or unknown.")
    explicit_source_usage: bool = Field(default=False)
    dataframe_like_usage: bool = Field(default=False)
    creates_dataframe_like: bool = Field(default=False)
    receives_dataframe_like: bool = Field(default=False)
    returns_dataframe_like: bool = Field(default=False)
    methods: list[str] = Field(default_factory=list)
    column_or_index_access: bool = Field(default=False)
    local_calls: list[str] = Field(default_factory=list)
    consumes_dataframe_from: list[str] = Field(
        default_factory=list,
        description=(
            "Names of top-level functions/classes — in this file or another "
            "analyzed file — that this symbol receives DataFrame-like input from."
        ),
    )
    confidence: str = Field(default="medium", description="low, medium, or high.")
    evidence: list[str] = Field(default_factory=list)


class PlannerV3FileAnalysis(BaseModel):
    file: str = Field(description="Relative file path.")
    symbols: list[PlannerV3SymbolAnalysis] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class PlannerV3SymbolAnalysisResult(BaseModel):
    files: list[PlannerV3FileAnalysis] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


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
    risk_level: str = Field(default="low", description="low, medium, or high.")
    risk_factors: list[str] = Field(default_factory=list)
    requires_human_review: bool = Field(default=False)
    human_review_reasons: list[str] = Field(default_factory=list)
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
        analysis_prompt = (_PROMPTS_DIR / "planner_v3_symbol_analysis.md").read_text(
            encoding="utf-8"
        )
        raw_llm = get_llm()
        llm = with_structured_output(raw_llm, PlannerV3Plan)
        analysis_llm = with_structured_output(raw_llm, PlannerV3SymbolAnalysisResult)
        self._chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", system_prompt),
                    ("human", _HUMAN_TEMPLATE),
                ]
            )
            | llm
        )
        self._symbol_analysis_chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", analysis_prompt),
                    ("human", _SYMBOL_ANALYSIS_TEMPLATE),
                ]
            )
            | analysis_llm
        )
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
        source_scope_files = (
            scan["affected_source_files"]
            if self._use_ast
            else scan["production_source_files"]
        )

        audit_log_name = (
            "project_audit.json"
            if replan_attempt == 0
            else f"project_audit_replan_{replan_attempt}.json"
        )
        (logs_dir / audit_log_name).write_text(
            json.dumps(audit, indent=2),
            encoding="utf-8",
        )

        file_contents = self._collect_file_contents(project_dir, source_scope_files)
        symbol_analysis = self._analyze_symbols(
            logs_dir=logs_dir,
            source_library=source_library,
            target_library=target_library,
            file_contents=file_contents,
            replan_attempt=replan_attempt,
        )
        symbol_analysis_payload = symbol_analysis.model_dump()
        dataframe_flow_analysis = _build_dataframe_flow_analysis(
            symbol_analysis_payload
        )

        result: PlannerV3Plan = self._chain.invoke(
            {
                "source_library": source_library,
                "target_library": target_library,
                "file_contents": file_contents,
                "dependency_files": scan["dependency_files"],
                "test_files": scan["test_files"],
                "affected_files": source_scope_files,
                "symbol_analysis": json.dumps(
                    symbol_analysis_payload,
                    indent=2,
                    sort_keys=True,
                ),
                "replan_context": self._build_replan_context(
                    replan_feedback,
                    replan_attempt,
                ),
            }
        )
        if result is None:
            raise RuntimeError("PlannerV3Agent did not return a structured plan.")

        affected_source_files = _planned_source_files(
            result,
            source_scope_files,
            use_ast=self._use_ast,
        )
        migration_steps, warnings, guardrail_events = self._normalize_steps(
            result.migration_steps,
            affected_source_files,
            scan["dependency_files"],
            audit["dependency_summary"],
            project_dir,
            symbol_analysis_payload,
            dataframe_flow_analysis,
            self._use_ast,
        )
        self._write_guardrail_log(logs_dir, guardrail_events, replan_attempt)

        plan = {
            "agent": self.name,
            "planner_version": "v3",
            "source_library": result.source_library,
            "target_library": result.target_library,
            "read_only": True,
            "diagnosis_use_ast": self._use_ast,
            "dependency_files": scan["dependency_files"],
            "dependency_summary": audit["dependency_summary"],
            "affected_files": affected_source_files,
            "affected_source_files": affected_source_files,
            "test_files_with_source_library_usage": scan[
                "test_files_with_source_library_usage"
            ],
            "related_tests": _sanitize_related_tests(
                result.related_tests,
                scan["test_files"],
            ),
            "complexity": _sanitize_complexity(
                result.complexity, affected_source_files
            ),
            "symbol_analysis": symbol_analysis_payload,
            "dataframe_flow_analysis": dataframe_flow_analysis,
            "planner_warnings": warnings,
            "migration_steps": migration_steps,
            "human_review_required": any(
                step.get("requires_human_review", False) for step in migration_steps
            ),
            "human_review_reasons": _human_review_reasons(migration_steps),
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

    def _analyze_symbols(
        self,
        *,
        logs_dir: Path,
        source_library: str,
        target_library: str,
        file_contents: str,
        replan_attempt: int,
    ) -> PlannerV3SymbolAnalysisResult:
        if not _env_flag("PLANNER_USE_SYMBOL_ANALYSIS", True):
            result = PlannerV3SymbolAnalysisResult(
                notes=["Symbol analysis disabled by PLANNER_USE_SYMBOL_ANALYSIS=0."]
            )
            self._write_symbol_analysis_log(logs_dir, result, replan_attempt)
            return result

        result: PlannerV3SymbolAnalysisResult | None = (
            self._symbol_analysis_chain.invoke(
                {
                    "source_library": source_library,
                    "target_library": target_library,
                    "file_contents": file_contents,
                }
            )
        )
        if result is None:
            result = PlannerV3SymbolAnalysisResult(
                notes=["Symbol analysis returned no structured output."]
            )
        self._write_symbol_analysis_log(logs_dir, result, replan_attempt)
        return result

    def _write_symbol_analysis_log(
        self,
        logs_dir: Path,
        result: PlannerV3SymbolAnalysisResult,
        replan_attempt: int,
    ) -> None:
        log_name = (
            "planner_symbol_analysis.json"
            if replan_attempt == 0
            else f"planner_symbol_analysis_replan_{replan_attempt}.json"
        )
        (logs_dir / log_name).write_text(
            json.dumps(result.model_dump(), indent=2),
            encoding="utf-8",
        )

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
        project_dir: Path,
        symbol_analysis: dict[str, Any],
        dataframe_flow_analysis: dict[str, Any],
        use_ast: bool,
    ) -> tuple[list[dict[str, Any]], list[str], list[PlannerGuardrailEvent]]:
        affected_targets = set(affected_source_files)
        dependency_targets = set(dependency_files)
        dependency_modification_allowed = (
            dependency_summary.get("target_dependency_action") == "add_dependency"
        )
        step_targets = set(affected_source_files)
        allowed_modification_targets = set(affected_source_files)
        if dependency_modification_allowed:
            step_targets |= dependency_targets
            allowed_modification_targets |= dependency_targets

        scoped_steps: list[dict[str, Any]] = []
        warnings: list[str] = []
        guardrail_events: list[PlannerGuardrailEvent] = []

        for step in steps:
            payload = step.model_dump()
            step_id = payload["step_id"]
            rel_file = payload["file"]
            if not _is_safe_relative_path(rel_file):
                _record_guardrail(
                    guardrail_events,
                    warnings,
                    rule="safe_relative_paths",
                    severity="error",
                    action="drop_step",
                    message=(f"Dropped step {step_id} for unsafe path {rel_file!r}."),
                    step_id=step_id,
                    file=rel_file,
                )
                continue

            if _is_test_file_path(rel_file):
                _record_guardrail(
                    guardrail_events,
                    warnings,
                    rule="test_file_target",
                    severity="error",
                    action="drop_step",
                    message=(
                        f"Dropped step {step_id} for {rel_file}: test files are "
                        "not migration targets."
                    ),
                    step_id=step_id,
                    file=rel_file,
                )
                continue

            if rel_file not in step_targets:
                _record_guardrail(
                    guardrail_events,
                    warnings,
                    rule="affected_files_only",
                    severity="error",
                    action="drop_step",
                    message=(
                        f"Dropped step {step_id} for {rel_file}: file is not an "
                        "affected production source file."
                    ),
                    step_id=step_id,
                    file=rel_file,
                )
                continue

            allowed_files = _sanitize_step_files(
                payload.get("allowed_files", []),
                allowed_modification_targets,
                guardrail_events,
                warnings,
                step_id,
                rel_file,
                field_name="allowed_files",
            )
            if rel_file not in allowed_files:
                allowed_files.insert(0, rel_file)

            grouped_files = _sanitize_step_files(
                payload.get("files", []),
                affected_targets,
                guardrail_events,
                warnings,
                step_id,
                rel_file,
                field_name="files",
            )
            if grouped_files and rel_file not in grouped_files:
                grouped_files.insert(0, rel_file)
            if grouped_files:
                for grouped_file in grouped_files:
                    if grouped_file not in allowed_files:
                        allowed_files.append(grouped_file)

            payload["status"] = "planned"
            payload["allowed_files"] = allowed_files
            payload["allowed_symbols"] = _valid_allowed_symbols(
                project_dir / payload["file"],
                payload.get("allowed_symbols", []),
                warnings,
                guardrail_events,
                step_id,
                payload["file"],
                use_ast,
            )
            payload["files"] = grouped_files
            payload["risk_level"] = _valid_risk_level(payload.get("risk_level"))
            payload["risk_factors"] = payload.get("risk_factors", [])
            payload["requires_human_review"] = bool(
                payload.get("requires_human_review", False)
            )
            payload["human_review_reasons"] = payload.get(
                "human_review_reasons",
                [],
            )
            _apply_human_review_policy(payload)
            payload.setdefault("step_type", "single_file")
            scoped_steps.extend(
                _least_scope_steps(
                    project_dir,
                    payload,
                    symbol_analysis,
                    dataframe_flow_analysis,
                    use_ast,
                    warnings,
                )
            )

        scoped_steps = _apply_flow_grouping(
            scoped_steps,
            dataframe_flow_analysis,
            guardrail_events,
            warnings,
        )

        normalized = _deduplicate_steps(scoped_steps, warnings)
        for index, payload in enumerate(normalized, start=1):
            payload["step_id"] = f"step_{index:03d}"

        if (
            normalized
            and dependency_summary.get("target_dependency_action") == "add_dependency"
            and "requirements.txt" in dependency_files
            and "requirements.txt" not in normalized[0]["allowed_files"]
        ):
            normalized[0]["allowed_files"].append("requirements.txt")
            _record_guardrail(
                guardrail_events,
                warnings,
                rule="target_dependency_scope",
                severity="info",
                action="allow_dependency_file",
                message=(
                    "Added requirements.txt to the first migration step because "
                    "the target dependency is not present."
                ),
                step_id=normalized[0]["step_id"],
                file="requirements.txt",
            )

        return normalized, warnings, guardrail_events

    def _write_guardrail_log(
        self,
        logs_dir: Path,
        events: list[PlannerGuardrailEvent],
        replan_attempt: int,
    ) -> None:
        log_name = (
            "planner_guardrails.json"
            if replan_attempt == 0
            else f"planner_guardrails_replan_{replan_attempt}.json"
        )
        (logs_dir / log_name).write_text(
            json.dumps([event.as_dict() for event in events], indent=2),
            encoding="utf-8",
        )


def _planned_source_files(
    plan: PlannerV3Plan,
    candidate_files: list[str],
    *,
    use_ast: bool,
) -> list[str]:
    if use_ast:
        return candidate_files

    candidates = set(candidate_files)
    selected = {rel_file for rel_file in plan.affected_files if rel_file in candidates}
    for step in plan.migration_steps:
        if step.file in candidates:
            selected.add(step.file)
        selected.update(rel_file for rel_file in step.files if rel_file in candidates)
        selected.update(
            rel_file for rel_file in step.allowed_files if rel_file in candidates
        )
    return [rel_file for rel_file in candidate_files if rel_file in selected]


def _valid_allowed_symbols(
    path: Path,
    symbols: list[str],
    warnings: list[str],
    guardrail_events: list[PlannerGuardrailEvent],
    step_id: str,
    rel_file: str,
    use_ast: bool,
) -> list[str]:
    if not symbols or path.suffix != ".py":
        return []
    if not use_ast:
        _record_guardrail(
            guardrail_events,
            warnings,
            rule="diagnosis_ast_disabled",
            severity="info",
            action="skip_allowed_symbols_ast_validation",
            message=(
                "DIAGNOSIS_USE_AST=0: kept allowed_symbols without AST-based "
                f"top-level symbol validation for {rel_file}."
            ),
            step_id=step_id,
            file=rel_file,
        )
        return symbols
    valid = _top_level_symbols(path)
    kept = [symbol for symbol in symbols if symbol in valid]
    removed = sorted(set(symbols) - set(kept))
    if removed:
        _record_guardrail(
            guardrail_events,
            warnings,
            rule="valid_allowed_symbols",
            severity="warning",
            action="remove_invalid_symbols",
            message=f"Removed invalid allowed_symbols from {rel_file}: {removed}.",
            step_id=step_id,
            file=rel_file,
            details={"removed": removed},
        )
    return kept


def _sanitize_related_tests(
    related_tests: list[str],
    discovered_tests: list[str],
) -> list[str]:
    discovered = set(discovered_tests)
    return [test for test in related_tests if test in discovered]


def _sanitize_complexity(
    complexity: dict[str, str],
    affected_source_files: list[str],
) -> dict[str, str]:
    valid_levels = {"low", "medium", "high"}
    sanitized = {}
    for rel_file in affected_source_files:
        level = complexity.get(rel_file, "medium")
        sanitized[rel_file] = level if level in valid_levels else "medium"
    return sanitized


def _valid_risk_level(level: str | None) -> str:
    return level if level in {"low", "medium", "high"} else "medium"


def _apply_human_review_policy(step: dict[str, Any]) -> None:
    reasons = [
        reason
        for reason in step.get("human_review_reasons", [])
        if isinstance(reason, str) and reason.strip()
    ]
    if step.get("risk_level") == "high":
        step["requires_human_review"] = True
        if not reasons:
            reasons.append("Step risk_level is high.")
    elif step.get("requires_human_review") and not reasons:
        reasons.append("Planner marked this step for human review.")
    step["human_review_reasons"] = reasons


def _human_review_reasons(steps: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    for step in steps:
        if not step.get("requires_human_review"):
            continue
        step_id = step.get("step_id", "unknown_step")
        rel_file = step.get("file", "unknown_file")
        for reason in step.get("human_review_reasons", []):
            message = f"{step_id} ({rel_file}): {reason}"
            if message not in reasons:
                reasons.append(message)
    return reasons


def _sanitize_step_files(
    files: list[str],
    allowed_targets: set[str],
    guardrail_events: list[PlannerGuardrailEvent],
    warnings: list[str],
    step_id: str,
    step_file: str,
    *,
    field_name: str,
) -> list[str]:
    kept: list[str] = []
    removed: list[str] = []
    for rel_path in files:
        if (
            not _is_safe_relative_path(rel_path)
            or _is_test_file_path(rel_path)
            or rel_path not in allowed_targets
        ):
            removed.append(rel_path)
            continue
        if rel_path not in kept:
            kept.append(rel_path)
    if removed:
        _record_guardrail(
            guardrail_events,
            warnings,
            rule="allowed_files_scope",
            severity="warning",
            action=f"sanitize_{field_name}",
            message=(
                f"Removed out-of-scope entries from {field_name} for "
                f"{step_file}: {removed}."
            ),
            step_id=step_id,
            file=step_file,
            details={"field": field_name, "removed": removed},
        )
    return kept


def _record_guardrail(
    events: list[PlannerGuardrailEvent],
    warnings: list[str],
    *,
    rule: str,
    severity: str,
    action: str,
    message: str,
    step_id: str | None = None,
    file: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    events.append(
        PlannerGuardrailEvent(
            rule=rule,
            severity=severity,
            action=action,
            message=message,
            step_id=step_id,
            file=file,
            details=details or {},
        )
    )
    warnings.append(message)


def _is_safe_relative_path(rel_path: str) -> bool:
    if not rel_path or Path(rel_path).is_absolute():
        return False
    parts = Path(rel_path).parts
    return ".." not in parts


def _is_test_file_path(rel_path: str) -> bool:
    path = Path(rel_path)
    if any(part in {"tests", "test", "testing"} for part in path.parts[:-1]):
        return True
    basename = path.name
    return basename.startswith("test_") or basename.endswith("_test.py")


def _build_dataframe_flow_analysis(symbol_analysis: dict[str, Any]) -> dict[str, Any]:
    """Derive ``dataframe_flow_analysis`` deterministically from symbol analysis.

    No extra LLM call: reuses the producer/consumer evidence already collected
    in the symbol-analysis phase (creates/returns/receives DataFrame-like plus
    ``consumes_dataframe_from``) to compute cross-file groups that must be
    planned together. This is read by ``MigrationAgent``'s upstream-failed-file
    check (``src/graph/migration_flow.py``) and by the post-validation semantic
    probe (``src/evaluation/semantic_probe.py``), so it must be populated even
    when the resulting plan is mostly single-file steps.
    """
    files = symbol_analysis.get("files", [])
    symbol_to_file: dict[str, str] = {}
    for file_analysis in files:
        rel_file = file_analysis.get("file", "")
        for symbol in file_analysis.get("symbols", []):
            name = symbol.get("name")
            if name and name not in symbol_to_file:
                symbol_to_file[name] = rel_file

    flow_symbols: list[dict[str, Any]] = []
    file_deps: dict[str, set[str]] = {}
    for file_analysis in files:
        rel_file = file_analysis.get("file", "")
        for symbol in file_analysis.get("symbols", []):
            name = symbol.get("name", "")
            consumes_from = [
                dep
                for dep in symbol.get("consumes_dataframe_from", [])
                if dep in symbol_to_file
            ]
            flow_symbols.append(
                {
                    "file": rel_file,
                    "symbol": name,
                    "role": _flow_role(symbol),
                    "returns_dataframe": bool(symbol.get("returns_dataframe_like")),
                    "consumes_dataframe_from": consumes_from,
                    "type_contract": "unknown",
                }
            )
            for dep in consumes_from:
                producer_file = symbol_to_file.get(dep)
                if producer_file and producer_file != rel_file:
                    file_deps.setdefault(rel_file, set()).add(producer_file)

    return {
        "symbols": flow_symbols,
        "groups": _build_flow_groups(flow_symbols, file_deps),
        "notes": symbol_analysis.get("notes", []),
    }


def _flow_role(symbol: dict[str, Any]) -> str:
    produces = bool(
        symbol.get("creates_dataframe_like") or symbol.get("returns_dataframe_like")
    )
    consumes = bool(symbol.get("receives_dataframe_like"))
    if produces and consumes:
        return "transformer"
    if produces:
        return "producer"
    if consumes:
        return "consumer"
    return "unknown"


def _build_flow_groups(
    flow_symbols: list[dict[str, Any]],
    file_deps: dict[str, set[str]],
) -> list[dict[str, Any]]:
    """Union consumer files with their cross-file producer files into groups."""
    if not file_deps:
        return []

    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            node = parent[node]
        return node

    def union(a: str, b: str) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_a] = root_b

    for consumer, producers in file_deps.items():
        for producer in producers:
            union(consumer, producer)

    clusters: dict[str, set[str]] = {}
    for node in parent:
        clusters.setdefault(find(node), set()).add(node)

    groups = []
    sorted_clusters = sorted(
        (sorted(members) for members in clusters.values() if len(members) > 1),
        key=lambda members: members[0],
    )
    for index, files in enumerate(sorted_clusters, start=1):
        file_set = set(files)
        symbols_in_group = sorted(
            {
                entry["symbol"]
                for entry in flow_symbols
                if entry["file"] in file_set
                and entry["role"] != "unknown"
                and entry["symbol"]
            }
        )
        groups.append(
            {
                "group_id": f"flow_group_{index:03d}",
                "files": files,
                "symbols": symbols_in_group,
                "reason": (
                    "Symbol analysis found DataFrame-like values produced in one "
                    "of these files and consumed in another."
                ),
                "planning_strategy": "grouped_before_consumers",
            }
        )
    return groups


def _file_level_flow_files(dataframe_flow_analysis: dict[str, Any]) -> set[str]:
    files: set[str] = set()
    for group in dataframe_flow_analysis.get("groups", []):
        group_files = [file for file in group.get("files", []) if isinstance(file, str)]
        if len(group_files) > 1:
            files.update(group_files)
    return files


def _apply_flow_grouping(
    steps: list[dict[str, Any]],
    dataframe_flow_analysis: dict[str, Any],
    guardrail_events: list[PlannerGuardrailEvent],
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Merge separate per-file steps that DataFrame flow analysis marked as coupled.

    Mirrors the per-step guard in ``_least_scope_steps`` (which keeps a coupled
    file from being split into symbol-level steps) by also keeping coupled
    *files* from being migrated as independent, unordered steps.
    """
    grouped_file_sets = [
        group["files"]
        for group in dataframe_flow_analysis.get("groups", [])
        if group.get("planning_strategy") == "grouped_before_consumers"
        and len(group.get("files", [])) > 1
    ]
    if not grouped_file_sets:
        return steps

    step_by_file: dict[str, dict[str, Any]] = {}
    for step in steps:
        for rel_file in step.get("files") or [step["file"]]:
            step_by_file.setdefault(rel_file, step)

    used_steps: set[int] = set()
    merged_steps: list[dict[str, Any]] = []
    for files in grouped_file_sets:
        present = [file for file in files if file in step_by_file]
        unique_steps = {id(step_by_file[file]) for file in present}
        if len(unique_steps) <= 1:
            continue  # already a single step covering the whole group

        ordered_files = _file_dependency_order(present, dataframe_flow_analysis)
        primary = dict(step_by_file[ordered_files[0]])
        allowed_files: list[str] = []
        for rel_file in ordered_files:
            step = step_by_file[rel_file]
            used_steps.add(id(step))
            for allowed in step.get("allowed_files", []):
                if allowed not in allowed_files:
                    allowed_files.append(allowed)
            if rel_file not in allowed_files:
                allowed_files.append(rel_file)

        primary["file"] = ordered_files[0]
        primary["files"] = ordered_files
        primary["allowed_files"] = allowed_files
        primary["allowed_symbols"] = []
        primary["step_type"] = "grouped"
        primary["description"] = _flow_group_description(
            ordered_files,
            dataframe_flow_analysis,
        )
        merged_steps.append(primary)
        _record_guardrail(
            guardrail_events,
            warnings,
            rule="dataframe_flow_grouping",
            severity="info",
            action="group_steps",
            message=(
                "Grouped DataFrame flow files into one atomic migration step: "
                + ", ".join(ordered_files)
            ),
            step_id=primary.get("step_id"),
            file=ordered_files[0],
        )

    if not merged_steps:
        return steps

    remaining = [step for step in steps if id(step) not in used_steps]
    return merged_steps + remaining


def _flow_group_description(
    files: list[str],
    dataframe_flow_analysis: dict[str, Any],
) -> str:
    file_set = set(files)
    symbols = [
        symbol
        for symbol in dataframe_flow_analysis.get("symbols", [])
        if symbol.get("file") in file_set and symbol.get("symbol")
    ]
    producers = [
        symbol["symbol"]
        for symbol in symbols
        if symbol.get("returns_dataframe") and not symbol.get("consumes_dataframe_from")
    ]
    consumers = [
        symbol
        for symbol in symbols
        if symbol.get("consumes_dataframe_from")
    ]
    parts = [
        "Migrate coupled DataFrame flow across "
        f"{_format_inline_list(files)} atomically."
    ]
    if producers:
        parts.append(
            "Preserve DataFrame producers "
            f"{_format_inline_list(producers)}."
        )
    if consumers:
        consumer_details = [
            f"{consumer['symbol']} consumes "
            f"{_format_inline_list(consumer.get('consumes_dataframe_from', []))}"
            for consumer in consumers[:4]
        ]
        if len(consumers) > 4:
            consumer_details.append(f"{len(consumers) - 4} more consumers")
        parts.append(
            "Preserve producer/consumer compatibility: "
            + "; ".join(consumer_details)
            + "."
        )
    return " ".join(parts)


def _format_inline_list(items: list[str]) -> str:
    cleaned = [item for item in items if item]
    if not cleaned:
        return "none"
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return ", ".join(cleaned[:-1]) + f", and {cleaned[-1]}"


def _file_dependency_order(
    files: list[str],
    dataframe_flow_analysis: dict[str, Any],
) -> list[str]:
    """Return *files* in topological order by cross-file producer-consumer links.

    Falls back to the original order on cycles or when no cross-file edges
    exist between the given files.
    """
    file_set = set(files)
    symbols = dataframe_flow_analysis.get("symbols", [])
    symbol_to_file: dict[str, str] = {
        s["symbol"]: s["file"]
        for s in symbols
        if s.get("symbol") and s.get("file") in file_set
    }

    file_deps: dict[str, set[str]] = {file: set() for file in files}
    for sym in symbols:
        consumer_file = sym.get("file")
        if consumer_file not in file_set:
            continue
        for producer_symbol in sym.get("consumes_dataframe_from", []):
            producer_file = symbol_to_file.get(producer_symbol)
            if producer_file and producer_file != consumer_file:
                file_deps[consumer_file].add(producer_file)

    if not any(file_deps.values()):
        return files

    in_degree = {file: len(file_deps[file]) for file in files}
    reverse: dict[str, set[str]] = {file: set() for file in files}
    for consumer, producers in file_deps.items():
        for producer in producers:
            if producer in reverse:
                reverse[producer].add(consumer)

    queue: deque[str] = deque(file for file in files if in_degree[file] == 0)
    result: list[str] = []
    while queue:
        file = queue.popleft()
        result.append(file)
        for consumer in sorted(reverse.get(file, set())):
            in_degree[consumer] -= 1
            if in_degree[consumer] == 0:
                queue.append(consumer)

    return result if len(result) == len(files) else files


def _least_scope_steps(
    project_dir: Path,
    step: dict[str, Any],
    symbol_analysis: dict[str, Any],
    dataframe_flow_analysis: dict[str, Any],
    use_ast: bool,
    warnings: list[str],
) -> list[dict[str, Any]]:
    if step.get("files") or step.get("allowed_symbols"):
        return [step]
    rel_file = step["file"]
    if not rel_file.endswith(".py"):
        return [step]
    if not use_ast:
        warnings.append(
            "DIAGNOSIS_USE_AST=0: skipped AST-based least-scope splitting for "
            f"{rel_file}."
        )
        return [step]

    if rel_file in _file_level_flow_files(dataframe_flow_analysis):
        warnings.append(
            f"Kept {rel_file} as one file-level step because DataFrame flow "
            "analysis marked it as coupled with other migration targets."
        )
        return [step]

    path = project_dir / rel_file
    symbols = _least_scope_candidate_symbols(path, rel_file, symbol_analysis)
    if len(symbols) <= 1:
        return [step]
    if _has_symbol_dependencies(path, symbols, symbol_analysis, rel_file):
        warnings.append(
            f"Kept {rel_file} as one file-level step because affected symbols "
            "call each other."
        )
        return [step]

    split_steps = []
    for symbol in symbols:
        scoped = dict(step)
        scoped["description"] = _least_scope_description(
            step.get("description", ""),
            rel_file,
            symbol,
            symbol_analysis,
        )
        scoped["allowed_symbols"] = [symbol]
        scoped["step_type"] = "single_symbol"
        split_steps.append(scoped)
    warnings.append(
        f"Applied least-scope planning: split {rel_file} into "
        f"{len(split_steps)} symbol-level steps."
    )
    return split_steps


def _least_scope_description(
    base_description: str,
    rel_file: str,
    symbol_name: str,
    symbol_analysis: dict[str, Any],
) -> str:
    prefix = base_description.strip() or f"Migrate {rel_file}."
    symbol = _symbol_analysis_for_symbol(symbol_analysis, rel_file, symbol_name)
    if not symbol:
        return f"{prefix} Limit this change to top-level symbol {symbol_name}."

    kind = symbol.get("kind") or "symbol"
    details: list[str] = []
    if symbol.get("explicit_source_usage"):
        details.append("uses source-library APIs directly")
    if symbol.get("dataframe_like_usage"):
        details.append("uses DataFrame-like operations")

    dataframe_roles = []
    if symbol.get("creates_dataframe_like"):
        dataframe_roles.append("creates")
    if symbol.get("receives_dataframe_like"):
        dataframe_roles.append("receives")
    if symbol.get("returns_dataframe_like"):
        dataframe_roles.append("returns")
    if dataframe_roles:
        details.append(
            f"{_format_inline_list(dataframe_roles)} DataFrame-like data"
        )

    methods = symbol.get("methods", [])
    if methods:
        details.append(f"key operations: {_format_inline_list(methods[:8])}")
    if symbol.get("column_or_index_access"):
        details.append("accesses columns or indexes")

    consumed_from = symbol.get("consumes_dataframe_from", [])
    if consumed_from:
        details.append(
            "consumes DataFrame output from "
            f"{_format_inline_list(consumed_from)}"
        )

    description = f"{prefix} Limit this change to top-level {kind} {symbol_name}."
    if details:
        description += " Preserve behavior for " + "; ".join(details) + "."

    evidence = [
        item
        for item in symbol.get("evidence", [])
        if isinstance(item, str) and item.strip()
    ]
    if evidence:
        description += " Evidence: " + " ".join(evidence[:2])
    return description


def _deduplicate_steps(
    steps: list[dict[str, Any]],
    warnings: list[str],
) -> list[dict[str, Any]]:
    seen: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    result = []
    for step in steps:
        key = (
            step.get("file", ""),
            tuple(step.get("files", [])),
            tuple(step.get("allowed_symbols", [])),
        )
        if key in seen:
            warnings.append(
                f"Dropped duplicate migration step for {step.get('file')} "
                f"with allowed_symbols {step.get('allowed_symbols', [])}."
            )
            continue
        seen.add(key)
        result.append(step)
    return result


def _top_level_symbols(path: Path) -> set[str]:
    tree = _parse_file(path)
    if tree is None:
        return set()
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _least_scope_candidate_symbols(
    path: Path,
    rel_file: str,
    symbol_analysis: dict[str, Any],
) -> list[str]:
    valid_symbols = _top_level_symbols(path)
    file_analysis = _symbol_analysis_for_file(symbol_analysis, rel_file)
    if not file_analysis:
        return []
    candidates = []
    for symbol in file_analysis.get("symbols", []):
        name = symbol.get("name")
        if name not in valid_symbols:
            continue
        if symbol.get("confidence") == "low":
            continue
        if symbol.get("explicit_source_usage") or symbol.get("dataframe_like_usage"):
            candidates.append(name)
    return candidates


def _has_symbol_dependencies(
    path: Path,
    symbols: list[str],
    symbol_analysis: dict[str, Any],
    rel_file: str,
) -> bool:
    symbol_set = set(symbols)
    file_analysis = _symbol_analysis_for_file(symbol_analysis, rel_file)
    if file_analysis:
        for symbol in file_analysis.get("symbols", []):
            name = symbol.get("name")
            if name not in symbol_set:
                continue
            if set(symbol.get("local_calls", [])) & (symbol_set - {name}):
                return True

    tree = _parse_file(path)
    if tree is None:
        return False
    top_level = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    for symbol_name in symbols:
        node = top_level.get(symbol_name)
        if node is not None and _calls_local_symbol(node, symbol_set - {symbol_name}):
            return True
    return False


def _parse_file(path: Path) -> ast.Module | None:
    if not path.exists() or path.suffix != ".py":
        return None
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return None


def _symbol_analysis_for_file(
    symbol_analysis: dict[str, Any],
    rel_file: str,
) -> dict[str, Any] | None:
    for file_analysis in symbol_analysis.get("files", []):
        if file_analysis.get("file") == rel_file:
            return file_analysis
    return None


def _symbol_analysis_for_symbol(
    symbol_analysis: dict[str, Any],
    rel_file: str,
    symbol_name: str,
) -> dict[str, Any] | None:
    file_analysis = _symbol_analysis_for_file(symbol_analysis, rel_file)
    if not file_analysis:
        return None
    for symbol in file_analysis.get("symbols", []):
        if symbol.get("name") == symbol_name:
            return symbol
    return None


def _calls_local_symbol(node: ast.AST, symbols: set[str]) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name) and child.func.id in symbols:
                return True
            if isinstance(child.func, ast.Attribute) and child.func.attr in symbols:
                return True
    return False
