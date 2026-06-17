from __future__ import annotations

import ast
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.llm import (
    format_llm_timeout_error,
    get_llm,
    is_llm_timeout_error,
    with_structured_output,
)
from src.migration_config import MigrationConfig
from src.tools.ast_transformer import apply_ast_transforms
from src.tools.pattern_scanner import (
    format_pattern_analysis,
    scan_for_confusing_patterns,
)

load_dotenv()

_PROMPTS_DIR = Path(__file__).parents[2] / "prompts"
MAX_MIGRATION_STRUCTURED_OUTPUT_ATTEMPTS = 2

_HUMAN_TEMPLATE = """\
Migrate the following file from {source_library} to {target_library}.

## Planned step
{description}

## File info
- File: {file}
- Allowed symbols to migrate: {allowed_symbols_str}

## Source code
```python
{source_code}
```

## Key Points
- Complete the migration fully. Every use of {source_library} must be replaced.
- Use idiomatic {target_library} code (e.g., `.filter()` instead of boolean indexing).
- Delete lines that don't apply in {target_library} (e.g., `.reset_index(drop=True)` for polars).
- Ensure the code is syntactically valid for Python 3.9+.
- Preserve all business logic and behavior.

{pattern_analysis}{retry_feedback_context}

Return ONLY the complete migrated file, preserving all untouched code outside the migration scope.
"""


# Fixed few-shot examples prepended to the prompt when `use_few_shot` is on.
# These target the patterns the model fails on most systematically (column
# mutation, dependent columns, groupby().transform, apply(axis=1)) — see
# ai_docs/improvements.md #11. They use placeholder column names (a/b/c,
# value/category) on purpose: the goal is to anchor the *transformation shape*
# and the structured-output format, NOT to encode a benchmark-specific answer
# key (that would reintroduce the overfitting v4/v5 were built to avoid).
_FEW_SHOT_PAIRS: list[tuple[str, dict[str, Any]]] = [
    (
        "Migrate the following file from pandas to polars.\n"
        "## Source code\n```python\n"
        "import pandas as pd\n\n\n"
        "def enrich(df):\n"
        '    df["base"] = df["a"] * df["b"]\n'
        '    df["score"] = df["base"] + df["c"]\n'
        "    return df\n"
        "```",
        {
            "migration_plan": (
                "df[col]=rhs is in-place mutation (missing in polars) -> with_columns. "
                "'score' reads 'base' created just above (staged-expression visibility) "
                "-> split into two sequential with_columns calls."
            ),
            "migrated_code": (
                "import polars as pl\n\n\n"
                "def enrich(df):\n"
                '    df = df.with_columns((pl.col("a") * pl.col("b")).alias("base"))\n'
                '    df = df.with_columns((pl.col("base") + pl.col("c")).alias("score"))\n'
                "    return df\n"
            ),
            "migrated_requirements": None,
            "changes_summary": (
                "Replaced two df[col]=rhs assignments with sequential with_columns calls "
                "so the dependent 'score' column sees the materialized 'base' column."
            ),
            "unmigrated_patterns": [],
        },
    ),
    (
        "Migrate the following file from pandas to polars.\n"
        "## Source code\n```python\n"
        "import pandas as pd\n\n\n"
        "def features(df):\n"
        '    df["grp_mean"] = df.groupby("category")["value"].transform("mean")\n'
        '    df["flag"] = df.apply(\n'
        '        lambda r: "hi" if r["value"] > r["grp_mean"] else "lo", axis=1\n'
        "    )\n"
        "    return df\n"
        "```",
        {
            "migration_plan": (
                "groupby().transform('mean') broadcasts a group aggregate back to every "
                "row -> mean().over('category'). apply(axis=1) is a row-wise conditional "
                "-> when/then/otherwise. 'flag' depends on 'grp_mean' -> sequential calls."
            ),
            "migrated_code": (
                "import polars as pl\n\n\n"
                "def features(df):\n"
                '    df = df.with_columns(pl.col("value").mean().over("category").alias("grp_mean"))\n'
                "    df = df.with_columns(\n"
                '        pl.when(pl.col("value") > pl.col("grp_mean"))\n'
                '        .then(pl.lit("hi"))\n'
                '        .otherwise(pl.lit("lo"))\n'
                '        .alias("flag")\n'
                "    )\n"
                "    return df\n"
            ),
            "migrated_requirements": None,
            "changes_summary": (
                "Rewrote groupby().transform as a window expression with over(), and the "
                "row-wise apply(axis=1) as a when/then/otherwise expression."
            ),
            "unmigrated_patterns": [],
        },
    ),
]


def _few_shot_messages(include_plan: bool = True) -> list[Any]:
    """Build alternating Human/AI demonstration messages for the prompt.

    Returned as concrete message instances (not ("human", str) tuples) so the
    braces in the example code are passed literally and never parsed as
    ChatPromptTemplate variables.

    When ``include_plan`` is False the demonstrated AI responses omit the
    ``migration_plan`` field, so the few-shot examples do not implicitly teach
    chain-of-thought. This keeps the "few-shot without CoT" ablation clean.
    """
    messages: list[Any] = []
    for human_text, ai_result in _FEW_SHOT_PAIRS:
        result = dict(ai_result)
        if not include_plan:
            result.pop("migration_plan", None)
        messages.append(HumanMessage(content=human_text))
        messages.append(AIMessage(content=json.dumps(result)))
    return messages


class UnmigratedPattern(BaseModel):
    line: int = Field(default=0, description="Line number in the original file.")
    api_call: str = Field(
        default="", description="Source-library API call that could not be migrated."
    )
    reason: str = Field(
        default="", description="Why no target-library equivalent exists."
    )


class MigrationResult(BaseModel):
    migrated_code: str = Field(
        description="The complete migrated file content, preserving all untouched code."
    )
    changes_summary: str = Field(
        description="Brief summary of the migration changes applied."
    )
    migrated_requirements: Optional[str] = Field(
        default=None,
        description="Updated requirements.txt content, or null if not changed by this step.",
    )
    unmigrated_patterns: list[UnmigratedPattern] = Field(
        default_factory=list,
        description="Source-library patterns that could not be migrated and require manual review.",
    )


class MigrationResultCoT(MigrationResult):
    """Schema bound when use_cot is on. `migration_plan` is REQUIRED here (no
    default) so the model's structured output is forced to emit it. With a
    default, Gemini's function-calling silently drops the field — making CoT
    unreliable — so the toggle swaps the bound schema instead of relying on a
    prompt instruction."""

    migration_plan: str = Field(
        description=(
            "Step-by-step reasoning produced for the code: invariants to preserve, scope, "
            "data flow, per-call mapping decisions, and risk-class flags. Required."
        ),
    )


class MigrationAgent:
    """LLM-powered agent that executes one planned migration step at a time."""

    name = "migration_agent"

    def __init__(self, config: MigrationConfig | None = None) -> None:
        self._config = config or MigrationConfig.from_env()
        # CoT lives in the prompt body (appending an instruction does not make
        # flash-lite fill migration_plan). So use_cot selects the base prompt:
        # v5 carries the CoT apparatus, v4 is its CoT-free twin. An explicit
        # MIGRATION_PROMPT_FILE still wins for ad-hoc overrides.
        prompt_file = os.getenv("MIGRATION_PROMPT_FILE") or (
            "migration_agent_v5.md" if self._config.use_cot else "migration_agent_v4.md"
        )
        system_prompt = (_PROMPTS_DIR / prompt_file).read_text(encoding="utf-8")
        # CoT on -> bind the schema where migration_plan is REQUIRED, forcing the
        # model to emit it. CoT off -> base schema has no migration_plan at all.
        result_schema = MigrationResultCoT if self._config.use_cot else MigrationResult
        llm = with_structured_output(get_llm(), result_schema)
        messages: list[Any] = [SystemMessage(content=system_prompt)]
        if self._config.use_few_shot:
            messages.extend(_few_shot_messages(include_plan=self._config.use_cot))
        messages.append(("human", _HUMAN_TEMPLATE))
        self._chain = ChatPromptTemplate.from_messages(messages) | llm
        self._current_unmigrated_patterns: list[dict[str, Any]] = []
        self._last_migration_plan: str = ""
        self._current_migration_plans: list[dict[str, Any]] = []

    @property
    def _cfg(self) -> MigrationConfig:
        # Tests build the agent via __new__/monkeypatched __init__ to exercise the
        # assisted pipeline; fall back to that preset when no config was injected.
        cfg = getattr(self, "_config", None)
        return cfg if cfg is not None else MigrationConfig.assisted()

    def run_step(
        self, project_dir: Path, step: dict[str, Any], logs_dir: Path
    ) -> dict[str, Any]:
        logs_dir.mkdir(parents=True, exist_ok=True)
        self._current_unmigrated_patterns = []
        if step.get("files"):
            return self._run_grouped_step(project_dir, step, logs_dir)

        rel_file = Path(step["file"])
        self._validate_step_scope(step, rel_file)
        target = project_dir / rel_file
        original = target.read_text(encoding="utf-8")

        retry_feedback = step.get("retry_feedback")
        migrated, total_attempts, last_error, pipeline = self._migrate_file_with_llm(
            rel_file,
            original,
            step,
            retry_feedback,
            logs_dir,
        )

        changed_files: list[str] = []
        if migrated != original:
            target.write_text(migrated, encoding="utf-8")
            changed_files.append(str(rel_file))

        if rel_file.name != "requirements.txt" and "requirements.txt" in step.get(
            "allowed_files", []
        ):
            if self._migrate_allowed_requirements(project_dir, step):
                changed_files.append("requirements.txt")

        result = {
            "agent": self.name,
            "step_id": step["step_id"],
            "file": step["file"],
            "allowed_symbols": step.get("allowed_symbols", []),
            "changed": bool(changed_files),
            "changed_files": changed_files,
            "status": "completed" if changed_files else "no_change",
            "retry_feedback_received": bool(retry_feedback),
            "unmigrated_patterns": self._current_unmigrated_patterns,
            "pipeline": pipeline,
        }
        if total_attempts:
            result["structured_output_attempts"] = total_attempts
        if last_error:
            result["structured_output_error"] = last_error
        (logs_dir / f"{step['step_id']}_migration.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        return result

    def _run_grouped_step(
        self,
        project_dir: Path,
        step: dict[str, Any],
        logs_dir: Path,
    ) -> dict[str, Any]:
        # Phase 1: migrate all files without writing to disk yet.
        # This prevents partial state where A uses Polars but B still uses pandas.
        pending: list[
            tuple[Path, str, str, str]
        ] = []  # (path, original, migrated, rel_str)
        file_results: list[dict[str, Any]] = []

        for rel_file_str in step.get("files", []):
            file_step = {
                **step,
                "file": rel_file_str,
                "allowed_symbols": [],
            }
            rel_file = Path(rel_file_str)
            self._validate_step_scope(file_step, rel_file)
            target = project_dir / rel_file
            original = target.read_text(encoding="utf-8")

            # Scope retry feedback to the current file so the LLM isn't confused
            # by errors from sibling files (e.g., a pivot-table error in features.py
            # passed verbatim to loaders.py causes structured-output failure).
            file_retry_feedback = _scoped_retry_feedback(
                step.get("retry_feedback"), rel_file_str
            )

            migrated, file_attempts, file_error, file_pipeline = (
                self._migrate_file_with_llm(
                    rel_file,
                    original,
                    file_step,
                    file_retry_feedback,
                    logs_dir,
                )
            )
            pending.append((target, original, migrated, rel_file_str))
            file_results.append(
                {
                    "file": str(rel_file),
                    "changed": migrated != original,
                    "structured_output_attempts": file_attempts,
                    "structured_output_error": file_error,
                    "pipeline": file_pipeline,
                }
            )

        # Phase 2: write atomically — only if every file produced valid LLM output.
        # A structured_output_error means the LLM gave up; writing the other files
        # would leave the project in a broken inter-file state.
        changed_files: list[str] = []
        has_llm_failure = any(r["structured_output_error"] for r in file_results)

        if not has_llm_failure:
            for target, _original, migrated, rel_file_str in pending:
                if migrated != _original:
                    target.write_text(migrated, encoding="utf-8")
                    changed_files.append(rel_file_str)

            if "requirements.txt" in step.get("allowed_files", []):
                if self._migrate_allowed_requirements(project_dir, step):
                    changed_files.append("requirements.txt")

        if has_llm_failure:
            status = "llm_failure"
        elif changed_files:
            status = "completed"
        else:
            status = "no_change"

        result = {
            "agent": self.name,
            "step_id": step["step_id"],
            "file": step["file"],
            "files": step.get("files", []),
            "allowed_symbols": [],
            "changed": bool(changed_files),
            "changed_files": changed_files,
            "file_results": file_results,
            "status": status,
            "retry_feedback_received": bool(step.get("retry_feedback")),
        }
        (logs_dir / f"{step['step_id']}_migration.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        return result

    def _migrate_file_with_llm(
        self,
        rel_file: Path,
        source: str,
        step: dict[str, Any],
        retry_feedback: dict[str, Any] | str | None,
        logs_dir: Path,
    ) -> tuple[str, int, str, dict[str, Any]]:
        if rel_file.name == "requirements.txt":
            return self._migrate_requirements(source, step), 0, "", _empty_pipeline()

        if rel_file.suffix != ".py":
            return source, 0, "", _empty_pipeline()

        allowed_symbols = step.get("allowed_symbols", [])
        self._last_migration_plan = ""
        self._current_migration_plans = []
        migrated, total_attempts, last_error = self._invoke_migration_chain(
            rel_file, source, step, retry_feedback
        )
        # Capture the unaided first-pass output before any post-processing so the
        # raw LLM signal can be measured even on assisted runs (see
        # ai_docs/proposal-research-mode.md). The migration_plan is the model's
        # first-pass chain-of-thought, captured here before regen/rescan overwrite it.
        raw_llm_code = migrated
        migration_plan = getattr(self, "_last_migration_plan", "")
        layers_active: list[str] = []
        cfg = self._cfg

        if allowed_symbols and cfg.enforce_symbol_scope:
            scoped = self._apply_allowed_symbol_scope(source, migrated, allowed_symbols)
            if scoped != migrated:
                layers_active.append("scope")
            migrated = scoped

        if cfg.regenerate_invalid_syntax:
            migrated, regen_attempts, regen_error = self._regenerate_if_invalid_python(
                rel_file, source, step, migrated, allowed_symbols
            )
            total_attempts += regen_attempts
            if regen_attempts:
                layers_active.append("syntax_regen")
            if regen_error:
                last_error = regen_error

        if cfg.use_rescan_retry:
            before_rescan = migrated
            migrated, rescan_attempts, rescan_error = (
                self._rescan_and_retry_if_patterns_remain(
                    rel_file, source, step, migrated, allowed_symbols
                )
            )
            total_attempts += rescan_attempts
            if migrated != before_rescan:
                layers_active.append("rescan")
            if rescan_error:
                last_error = rescan_error

        changed_by_ast = False
        if cfg.use_ast_fallback and rel_file.suffix == ".py":
            source_library = step.get("source_library", "pandas")
            ast_result = apply_ast_transforms(migrated, source_library)
            changed_by_ast = ast_result.code != migrated
            if changed_by_ast:
                layers_active.append("ast")
            migrated = ast_result.code

        pipeline = {
            "migration_plan": migration_plan,
            "migration_plans": list(getattr(self, "_current_migration_plans", [])),
            "raw_llm_code": raw_llm_code,
            "final_code": migrated,
            "raw_equals_final": raw_llm_code == migrated,
            "changed_by_ast": changed_by_ast,
            "layers_active": layers_active,
        }
        return migrated, total_attempts, last_error, pipeline

    def _regenerate_if_invalid_python(
        self,
        rel_file: Path,
        source: str,
        step: dict[str, Any],
        migrated: str,
        allowed_symbols: list[str],
    ) -> tuple[str, int, str]:
        syntax_error = self._validate_python39_syntax(migrated)
        if not syntax_error:
            return migrated, 0, ""
        regenerated, attempts, error = self._invoke_migration_chain(
            rel_file,
            source,
            step,
            {
                "feedback_for_agent": (
                    "The previous migrated file was not valid Python and "
                    "must be regenerated as a complete, syntactically valid "
                    f"file. Syntax feedback: {syntax_error}"
                )
            },
            phase="syntax_regen",
        )
        if allowed_symbols:
            regenerated = self._apply_allowed_symbol_scope(
                source, regenerated, allowed_symbols
            )
        return regenerated, attempts, error

    def _invoke_migration_chain(
        self,
        rel_file: Path,
        source: str,
        step: dict[str, Any],
        retry_feedback: dict[str, Any] | str | None,
        phase: str = "initial",
    ) -> tuple[str, int, str]:
        allowed_symbols = step.get("allowed_symbols", [])
        allowed_symbols_str = (
            ", ".join(allowed_symbols) if allowed_symbols else "(all code in file)"
        )

        retry_feedback_context = ""
        if retry_feedback:
            retry_feedback_context = _retry_feedback_context(retry_feedback)

        source_library = step.get("source_library", "pandas")
        hits = (
            scan_for_confusing_patterns(source, source_library, allowed_symbols or None)
            if self._cfg.use_pattern_scanner
            else []
        )
        prompt_payload = {
            "source_library": source_library,
            "target_library": step.get("target_library", "polars"),
            "file": str(rel_file),
            "description": step.get("description", "Migrate this file."),
            "allowed_symbols_str": allowed_symbols_str,
            "source_code": source,
            "pattern_analysis": format_pattern_analysis(hits),
            "retry_feedback_context": retry_feedback_context,
        }
        for attempt in range(1, MAX_MIGRATION_STRUCTURED_OUTPUT_ATTEMPTS + 1):
            try:
                result: MigrationResult | None = self._chain.invoke(prompt_payload)
            except Exception as exc:
                if is_llm_timeout_error(exc):
                    return (
                        source,
                        attempt,
                        format_llm_timeout_error(
                            f"migration step {step['step_id']} ({phase}) for {rel_file}",
                            exc,
                        ),
                    )
                raise
            migrated_code = getattr(result, "migrated_code", None)
            if isinstance(migrated_code, str):
                plan = getattr(result, "migration_plan", "") or ""
                self._last_migration_plan = plan
                self._current_migration_plans.append(
                    {"phase": phase, "attempt": attempt, "migration_plan": plan}
                )
                patterns = getattr(result, "unmigrated_patterns", None)
                if patterns:
                    self._current_unmigrated_patterns = [
                        p.model_dump() if hasattr(p, "model_dump") else dict(p)
                        for p in patterns
                    ]
                return migrated_code, attempt, ""

        return (
            source,
            MAX_MIGRATION_STRUCTURED_OUTPUT_ATTEMPTS,
            "MigrationAgent returned no structured output.",
        )

    def _apply_allowed_symbol_scope(
        self,
        original: str,
        migrated: str,
        allowed_symbols: list[str],
    ) -> str:
        try:
            original_tree = ast.parse(original)
            migrated_tree = ast.parse(migrated)
        except SyntaxError:
            return original

        migrated_symbols = {
            node.name: node
            for node in migrated_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and hasattr(node, "end_lineno")
        }
        if not migrated_symbols:
            return original

        original_lines = original.splitlines(keepends=True)
        migrated_lines = migrated.splitlines(keepends=True)
        replacements: list[tuple[int, int, str]] = []
        for node in original_tree.body:
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            if node.name not in allowed_symbols or not hasattr(node, "end_lineno"):
                continue
            migrated_node = migrated_symbols.get(node.name)
            if not migrated_node:
                continue
            replacement = "".join(
                migrated_lines[migrated_node.lineno - 1 : migrated_node.end_lineno]
            )
            replacements.append((node.lineno - 1, node.end_lineno, replacement))

        if not replacements:
            return original

        for start, end, replacement in reversed(replacements):
            original_lines[start:end] = [replacement]
        scoped = "".join(original_lines)
        if "pl." in scoped and "import polars as pl" not in scoped:
            scoped = self._ensure_polars_import(scoped)
        scoped = self._remove_unused_pandas_alias_import(scoped)
        return scoped

    def _rescan_and_retry_if_patterns_remain(
        self,
        rel_file: Path,
        source: str,
        step: dict[str, Any],
        migrated: str,
        allowed_symbols: list[str],
    ) -> tuple[str, int, str]:
        """Re-scan migrated code and retry once if source-library patterns remain."""
        if rel_file.suffix != ".py":
            return migrated, 0, ""
        source_library = step.get("source_library", "pandas")
        remaining = scan_for_confusing_patterns(
            migrated, source_library, allowed_symbols or None
        )
        if not remaining:
            return migrated, 0, ""
        migrated_lines = migrated.splitlines()
        pattern_items = []
        for hit in remaining:
            line_content = (
                migrated_lines[hit.line - 1].strip()
                if 0 < hit.line <= len(migrated_lines)
                else ""
            )
            pattern_items.append(
                f"- [ ] Line {hit.line}: `{line_content}`\n"
                f"      Required fix: {hit.guidance}"
            )
        feedback = {
            "feedback_for_agent": (
                f"Post-migration scan found {len(remaining)} unconverted pattern(s) "
                "in the migrated code. The lines below still use source-library syntax "
                "and MUST be rewritten before returning:\n\n"
                + "\n".join(pattern_items)
                + "\n\nFor each line above, replace the shown code with the "
                "target-library equivalent described in 'Required fix'. "
                "Return the complete corrected file."
            )
        }
        revised, attempts, error = self._invoke_migration_chain(
            rel_file, source, step, feedback, phase="rescan"
        )
        if allowed_symbols:
            revised = self._apply_allowed_symbol_scope(source, revised, allowed_symbols)
        return revised, attempts, error

    def _ensure_polars_import(self, source: str) -> str:
        lines = source.splitlines(keepends=True)
        insert_at = 0
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("from __future__ import"):
                insert_at = index + 1
                continue
            if stripped.startswith("import ") or stripped.startswith("from "):
                insert_at = index + 1
                continue
            if stripped:
                break
        lines.insert(insert_at, "import polars as pl\n")
        return "".join(lines)

    def _remove_unused_pandas_alias_import(self, source: str) -> str:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source

        # Collect every 'import pandas as <alias>' alias name.
        pandas_aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "pandas" and alias.asname:
                        pandas_aliases.add(alias.asname)

        if not pandas_aliases:
            return source

        # An alias is "used" only when it appears as the object of an attribute
        # access (e.g. pd.DataFrame), not when it merely appears in a comment or
        # string literal.
        referenced = {
            node.value.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in pandas_aliases
        }

        unused = pandas_aliases - referenced
        if not unused:
            return source

        result = source
        for alias in unused:
            result = re.sub(
                rf"^import pandas as {re.escape(alias)}\n+",
                "",
                result,
                flags=re.MULTILINE,
            )
        return result

    def _validate_python39_syntax(self, code: str) -> str | None:
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return f"Syntax error on line {e.lineno}: {e.msg}"
        return _check_pep604_union_types(tree)

    def _validate_step_scope(self, step: dict[str, Any], rel_file: Path) -> None:
        allowed_files = set(step.get("allowed_files", []))
        if str(rel_file) not in allowed_files:
            raise ValueError(
                f"Step {step['step_id']} targets {rel_file}, "
                "but that file is not listed in allowed_files."
            )

    def _migrate_allowed_requirements(
        self, project_dir: Path, step: dict[str, Any]
    ) -> bool:
        requirements = project_dir / "requirements.txt"
        if not requirements.exists():
            return False
        original = requirements.read_text(encoding="utf-8")
        migrated = self._migrate_requirements(original, step)
        if migrated == original:
            return False
        requirements.write_text(migrated, encoding="utf-8")
        return True

    def _migrate_requirements(self, source: str, step: dict[str, Any]) -> str:
        target_library = step.get("target_library")
        if not target_library:
            raise ValueError(
                f"Step {step['step_id']} targets requirements.txt, "
                "but no target_library was provided by diagnosis."
            )
        result = self._remove_package_from_requirements(
            source, step.get("source_library", "")
        )
        if self._requirements_contains_package(result, target_library):
            return result
        suffix = "" if result.endswith("\n") else "\n"
        if self._uses_hash_locked_requirements(result):
            dependency = self._resolve_hashed_requirement(target_library)
        else:
            dependency = target_library
        return result + suffix + dependency + "\n"

    def _remove_package_from_requirements(self, source: str, package_name: str) -> str:
        if not package_name:
            return source
        normalized = _normalize_package_name(package_name)
        lines = source.splitlines(keepends=True)
        filtered = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("-"):
                filtered.append(line)
                continue
            match = re.match(
                r"(?P<name>[A-Za-z0-9_.-]+)\s*(?P<constraint>.*)", stripped
            )
            if match and _normalize_package_name(match.group("name")) == normalized:
                continue
            filtered.append(line)
        return "".join(filtered)

    def _requirements_contains_package(self, source: str, package_name: str) -> bool:
        normalized_package = _normalize_package_name(package_name)
        for raw_line in source.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            match = re.match(r"(?P<name>[A-Za-z0-9_.-]+)\s*(?P<constraint>.*)", line)
            if (
                match
                and _normalize_package_name(match.group("name")) == normalized_package
            ):
                return True
        return False

    def _uses_hash_locked_requirements(self, source: str) -> bool:
        return "--hash=sha256:" in source

    def _resolve_hashed_requirement(self, package_name: str) -> str:
        return "\n".join(
            self._resolve_hashed_requirement_blocks(
                package_name, version=None, seen=set()
            )
        )

    def _resolve_hashed_requirement_blocks(
        self,
        package_name: str,
        version: str | None,
        seen: set[str],
    ) -> list[str]:
        metadata = self._fetch_pypi_package_metadata(package_name, version)
        version = metadata["info"]["version"]
        normalized_key = f"{_normalize_package_name(package_name)}=={version}"
        if normalized_key in seen:
            return []
        seen.add(normalized_key)

        files = metadata.get("releases", {}).get(version, metadata.get("urls", []))
        hashes = sorted(
            {
                file_info.get("digests", {}).get("sha256")
                for file_info in files
                if file_info.get("digests", {}).get("sha256")
            }
        )
        if not hashes:
            raise RuntimeError(
                f"Could not resolve sha256 hashes for {package_name}=={version} from PyPI."
            )

        lines = [f"{package_name}=={version} \\"]
        for index, digest in enumerate(hashes):
            continuation = " \\" if index < len(hashes) - 1 else ""
            lines.append(f"    --hash=sha256:{digest}{continuation}")
        blocks = ["\n".join(lines)]
        for requirement in metadata["info"].get("requires_dist") or []:
            pinned_dependency = _pinned_runtime_dependency(requirement)
            if not pinned_dependency:
                continue
            dependency_name, dependency_version = pinned_dependency
            blocks.extend(
                self._resolve_hashed_requirement_blocks(
                    dependency_name,
                    version=dependency_version,
                    seen=seen,
                )
            )
        return blocks

    def _fetch_pypi_package_metadata(
        self,
        package_name: str,
        version: str | None = None,
    ) -> dict[str, Any]:
        if version:
            url = f"https://pypi.org/pypi/{package_name}/{version}/json"
        else:
            url = f"https://pypi.org/pypi/{package_name}/json"
        with urllib.request.urlopen(url, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))


def _empty_pipeline() -> dict[str, Any]:
    """Pipeline record for files that never go through the LLM migration chain
    (requirements.txt, non-Python files)."""
    return {
        "raw_llm_code": None,
        "final_code": None,
        "raw_equals_final": True,
        "changed_by_ast": False,
        "layers_active": [],
    }


def _normalize_package_name(name: str) -> str:
    return name.replace("_", "-").lower()


def _retry_feedback_context(retry_feedback: dict[str, Any] | str) -> str:
    if not isinstance(retry_feedback, dict):
        return (
            "\n## Retry feedback from validation or implementation review\n"
            f"{retry_feedback}"
        )

    feedback_text = retry_feedback.get("feedback_for_agent", "No specific feedback.")
    repair_plan = retry_feedback.get("repair_plan")
    validation_feedback = retry_feedback.get("validation_feedback")
    parts = [
        "## Retry feedback from validation or implementation review",
        str(feedback_text),
    ]
    if repair_plan:
        parts.extend(
            [
                "",
                "## Structured Repair Plan",
                "Treat this plan as mandatory for the next migrated file. "
                "Before returning code, verify every acceptance criterion and "
                "avoid every forbidden pattern.",
                json.dumps(repair_plan, indent=2, sort_keys=True),
            ]
        )
        acceptance = repair_plan.get("acceptance_criteria") or []
        if acceptance:
            parts.extend(
                [
                    "",
                    "## Mandatory Acceptance Criteria",
                    *[f"- {item}" for item in acceptance],
                ]
            )
        must_not_do = repair_plan.get("must_not_do") or []
        if must_not_do:
            parts.extend(
                [
                    "",
                    "## Forbidden Patterns For This Retry",
                    *[f"- {item}" for item in must_not_do],
                ]
            )
    if validation_feedback:
        parts.extend(
            [
                "",
                "## Original Validation Feedback",
                str(validation_feedback),
            ]
        )
    return "\n" + "\n".join(parts)


def _check_pep604_union_types(tree: ast.Module) -> str | None:
    """Return an error message if the code uses X | Y in annotations without
    'from __future__ import annotations'.

    In Python 3.9, X | Y is bitwise OR (valid syntax) but causes a TypeError
    at annotation-evaluation time when X or Y are type objects.  With the
    __future__ import, annotations are stored as strings and never evaluated,
    so the expression is safe.  Python 3.10+ natively supports the union type.
    """
    for node in tree.body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "__future__"
            and any(alias.name == "annotations" for alias in node.names)
        ):
            return None

    annotation_nodes: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            annotation_nodes.append(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                annotation_nodes.append(node.returns)
            for arg in (
                node.args.args
                + node.args.posonlyargs
                + node.args.kwonlyargs
                + ([node.args.vararg] if node.args.vararg else [])
                + ([node.args.kwarg] if node.args.kwarg else [])
            ):
                if arg.annotation is not None:
                    annotation_nodes.append(arg.annotation)

    for annotation in annotation_nodes:
        for subnode in ast.walk(annotation):
            if isinstance(subnode, ast.BinOp) and isinstance(subnode.op, ast.BitOr):
                return (
                    f"Line {subnode.lineno}: X | Y union syntax in annotations "
                    "causes a TypeError at runtime on Python 3.9. Add "
                    "'from __future__ import annotations' at the top of the file "
                    "or use Union[X, Y] from the typing module instead."
                )
    return None


def _scoped_retry_feedback(
    retry_feedback: dict[str, Any] | str | None,
    rel_file_str: str,
) -> dict[str, Any] | str | None:
    """Return retry feedback scoped to a specific file within a grouped step.

    In grouped steps the same validation feedback is reused for all files, but
    it may describe errors that belong only to a sibling file. Passing irrelevant
    context (e.g. a pivot-table error) to a simple loader file confuses the LLM
    and causes structured-output failures. We prefix the feedback with an explicit
    "you are migrating <file>" anchor so the model stays on-task.
    """
    if not retry_feedback:
        return retry_feedback
    if isinstance(retry_feedback, dict):
        original_text = retry_feedback.get("feedback_for_agent", "")
        return {
            **retry_feedback,
            "feedback_for_agent": (
                f"You are migrating `{rel_file_str}`. "
                "Focus only on what needs to change in this file.\n\n" + original_text
            ),
        }
    return (
        f"You are migrating `{rel_file_str}`. "
        "Focus only on what needs to change in this file.\n\n" + str(retry_feedback)
    )


def _pinned_runtime_dependency(requirement: str) -> tuple[str, str] | None:
    if ";" in requirement:
        return None
    match = re.match(
        r"(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[A-Za-z0-9_.!+*-]+)$",
        requirement.strip(),
    )
    if not match:
        return None
    return match.group("name"), match.group("version")
