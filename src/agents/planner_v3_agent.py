from __future__ import annotations

import ast
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

        migration_steps, warnings = self._normalize_steps(
            result.migration_steps,
            affected_source_files,
            scan["dependency_files"],
            audit["dependency_summary"],
            project_dir,
            symbol_analysis_payload,
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
        result: PlannerV3SymbolAnalysisResult | None = self._symbol_analysis_chain.invoke({
            "source_library": source_library,
            "target_library": target_library,
            "file_contents": file_contents,
        })
        if result is None:
            result = PlannerV3SymbolAnalysisResult(
                notes=["Symbol analysis returned no structured output."]
            )
        log_name = (
            "planner_symbol_analysis.json"
            if replan_attempt == 0
            else f"planner_symbol_analysis_replan_{replan_attempt}.json"
        )
        (logs_dir / log_name).write_text(
            json.dumps(result.model_dump(), indent=2),
            encoding="utf-8",
        )
        return result

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
    ) -> tuple[list[dict[str, Any]], list[str]]:
        allowed_targets = set(affected_source_files) | set(dependency_files)
        scoped_steps: list[dict[str, Any]] = []
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

            payload["status"] = "planned"
            payload["allowed_files"] = allowed_files
            payload["allowed_symbols"] = _valid_allowed_symbols(
                project_dir / payload["file"],
                payload.get("allowed_symbols", []),
                warnings,
                payload["file"],
            )
            payload["files"] = payload.get("files", [])
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
            warnings.append(
                "Added requirements.txt to the first migration step because the "
                "target dependency is not present."
            )

        return normalized, warnings


def _valid_allowed_symbols(
    path: Path,
    symbols: list[str],
    warnings: list[str],
    rel_file: str,
) -> list[str]:
    if not symbols or path.suffix != ".py":
        return []
    valid = _top_level_symbols(path)
    kept = [symbol for symbol in symbols if symbol in valid]
    removed = sorted(set(symbols) - set(kept))
    if removed:
        warnings.append(
            f"Removed invalid allowed_symbols from {rel_file}: {removed}."
        )
    return kept


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
