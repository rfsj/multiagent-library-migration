from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.llm import get_llm
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

## Source files with {source_library} usage

{file_contents}

## Structural metadata (from static analysis)

- Dependency files: {dependency_files}
- Test files: {test_files}
- Affected files: {affected_files}

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
        llm = raw_llm.with_structured_output(PlannerV3Plan)
        analysis_llm = raw_llm.with_structured_output(PlannerV3SymbolAnalysisResult)
        self._chain = (
            ChatPromptTemplate.from_messages([
                ("system", system_prompt),
                ("human", _HUMAN_TEMPLATE),
            ])
            | llm
        )
        self._symbol_analysis_chain = (
            ChatPromptTemplate.from_messages([
                ("system", analysis_prompt),
                ("human", _SYMBOL_ANALYSIS_TEMPLATE),
            ])
            | analysis_llm
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

        file_contents = self._collect_file_contents(project_dir, affected_source_files)
        symbol_analysis = self._analyze_symbols(
            logs_dir=logs_dir,
            source_library=source_library,
            target_library=target_library,
            file_contents=file_contents,
            replan_attempt=replan_attempt,
        )
        symbol_analysis_payload = symbol_analysis.model_dump()

        result: PlannerV3Plan = self._chain.invoke({
            "source_library": source_library,
            "target_library": target_library,
            "file_contents": file_contents,
            "dependency_files": scan["dependency_files"],
            "test_files": scan["test_files"],
            "affected_files": affected_source_files,
            "symbol_analysis": json.dumps(
                symbol_analysis_payload,
                indent=2,
                sort_keys=True,
            ),
            "replan_context": self._build_replan_context(
                replan_feedback,
                replan_attempt,
            ),
        })
        if result is None:
            raise RuntimeError("PlannerV3Agent did not return a structured plan.")

        migration_steps, warnings, guardrail_events = self._normalize_steps(
            result.migration_steps,
            affected_source_files,
            scan["dependency_files"],
            audit["dependency_summary"],
            project_dir,
            symbol_analysis_payload,
        )
        self._write_guardrail_log(logs_dir, guardrail_events, replan_attempt)

        plan = {
            "agent": self.name,
            "planner_version": "v3",
            "source_library": result.source_library,
            "target_library": result.target_library,
            "read_only": True,
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
            "complexity": _sanitize_complexity(result.complexity, affected_source_files),
            "symbol_analysis": symbol_analysis_payload,
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

        result: PlannerV3SymbolAnalysisResult | None = self._symbol_analysis_chain.invoke({
            "source_library": source_library,
            "target_library": target_library,
            "file_contents": file_contents,
        })
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
        project_dir: Path,
        symbol_analysis: dict[str, Any],
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
                    message=(
                        f"Dropped step {step_id} for unsafe path {rel_file!r}."
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
            )
            payload["files"] = grouped_files
            payload.setdefault("step_type", "single_file")
            scoped_steps.extend(
                _least_scope_steps(
                    project_dir,
                    payload,
                    symbol_analysis,
                    warnings,
                )
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


def _valid_allowed_symbols(
    path: Path,
    symbols: list[str],
    warnings: list[str],
    guardrail_events: list[PlannerGuardrailEvent],
    step_id: str,
    rel_file: str,
) -> list[str]:
    if not symbols or path.suffix != ".py":
        return []
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
        if not _is_safe_relative_path(rel_path) or rel_path not in allowed_targets:
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


def _least_scope_steps(
    project_dir: Path,
    step: dict[str, Any],
    symbol_analysis: dict[str, Any],
    warnings: list[str],
) -> list[dict[str, Any]]:
    if step.get("files") or step.get("allowed_symbols"):
        return [step]
    rel_file = step["file"]
    if not rel_file.endswith(".py"):
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
        scoped["description"] = f"Migrate {symbol} in {rel_file}."
        scoped["allowed_symbols"] = [symbol]
        scoped["step_type"] = "single_symbol"
        split_steps.append(scoped)
    warnings.append(
        f"Applied least-scope planning: split {rel_file} into "
        f"{len(split_steps)} symbol-level steps."
    )
    return split_steps


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


def _calls_local_symbol(node: ast.AST, symbols: set[str]) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name) and child.func.id in symbols:
                return True
            if isinstance(child.func, ast.Attribute) and child.func.attr in symbols:
                return True
    return False
