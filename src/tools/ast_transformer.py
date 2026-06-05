"""Deterministic AST-based pandas→polars fallback transformer.

Applied after LLM migration when MIGRATION_AST_FALLBACK=1 is set.
Only converts patterns that map mechanically without semantic ambiguity.

Transforms implemented:
- df["col"] = rhs  →  df = df.with_columns(polars_rhs.alias("col"))
  (dependent columns are split into sequential with_columns calls)
- .reset_index(drop=True)  →  removed from the expression chain
- .sort_values(by, ascending=...)  →  .sort(by, descending=...)
"""
from __future__ import annotations

import ast
import copy
import os
from dataclasses import dataclass, field


@dataclass
class TransformResult:
    code: str
    applied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def ast_fallback_enabled() -> bool:
    """Return True when MIGRATION_AST_FALLBACK env var is truthy."""
    return os.environ.get("MIGRATION_AST_FALLBACK", "").lower() in ("1", "true", "yes")


def apply_ast_transforms(source: str, source_library: str = "pandas") -> TransformResult:
    """Apply deterministic pandas→polars transforms to *source*.

    Runs three independent passes; each re-parses the output of the previous
    so line numbers stay consistent.
    """
    if source_library != "pandas":
        return TransformResult(code=source)

    applied: list[str] = []
    skipped: list[str] = []

    for transform in (
        _pass_column_assignments,
        _pass_remove_reset_index,
        _pass_sort_values,
    ):
        result = transform(source)
        source = result.code
        applied.extend(result.applied)
        skipped.extend(result.skipped)

    return TransformResult(code=source, applied=applied, skipped=skipped)


# ---------------------------------------------------------------------------
# Pass 1: df["col"] = rhs  →  df = df.with_columns(polars_rhs.alias("col"))
# ---------------------------------------------------------------------------

def _pass_column_assignments(source: str) -> TransformResult:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return TransformResult(code=source, skipped=["syntax_error"])

    source_lines = source.splitlines(keepends=True)
    replacements: list[tuple[int, int, str]] = []  # (start_0idx, end_0idx_excl, new_line)
    applied: list[str] = []
    skipped: list[str] = []

    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        indent = _stmt_indent(func.body[0], source_lines) if func.body else ""
        _collect_column_assign_replacements(
            func.body, source_lines, indent, replacements, applied, skipped
        )

    if not replacements:
        return TransformResult(code=source, applied=applied, skipped=skipped)

    replacements.sort(key=lambda r: r[0], reverse=True)
    lines = list(source_lines)
    for start, end, new_line in replacements:
        lines[start:end] = [new_line]

    return TransformResult(code="".join(lines), applied=applied, skipped=skipped)


def _collect_column_assign_replacements(
    stmts: list[ast.stmt],
    source_lines: list[str],
    indent: str,
    replacements: list[tuple[int, int, str]],
    applied: list[str],
    skipped: list[str],
) -> None:
    for stmt in stmts:
        # Recurse into nested bodies (if/for/with/try)
        for attr in ("body", "orelse", "finalbody", "handlers"):
            for child in getattr(stmt, attr, []) or []:
                if isinstance(child, ast.stmt):
                    child_indent = _stmt_indent(child, source_lines)
                    _collect_column_assign_replacements(
                        [child], source_lines, child_indent, replacements, applied, skipped
                    )

        if not isinstance(stmt, ast.Assign):
            continue
        if len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if not (
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and isinstance(target.slice, ast.Constant)
            and isinstance(target.slice.value, str)
        ):
            continue

        df_var = target.value.id
        col_name = target.slice.value

        if _has_source_library_calls(stmt.value):
            skipped.append(
                f"line {stmt.lineno}: {df_var}[\"{col_name}\"] = ... "
                "skipped — RHS contains library function calls"
            )
            continue

        rhs_copy = copy.deepcopy(stmt.value)
        new_rhs = _ColumnRefToPolarsCol(df_var).visit(rhs_copy)
        ast.fix_missing_locations(new_rhs)
        rhs_text = ast.unparse(new_rhs)

        stmt_indent = _stmt_indent(stmt, source_lines)
        new_line = (
            f"{stmt_indent}{df_var} = {df_var}.with_columns("
            f"({rhs_text}).alias(\"{col_name}\"))\n"
        )
        start = stmt.lineno - 1
        end = stmt.end_lineno
        replacements.append((start, end, new_line))
        applied.append(
            f"line {stmt.lineno}: {df_var}[\"{col_name}\"] = ... "
            f"→ {df_var} = {df_var}.with_columns(...)"
        )


class _ColumnRefToPolarsCol(ast.NodeTransformer):
    """Replace var["col"] with pl.col("col") inside expressions."""

    def __init__(self, df_var: str) -> None:
        self._df_var = df_var

    def visit_Subscript(self, node: ast.Subscript) -> ast.expr:
        self.generic_visit(node)
        if (
            isinstance(node.value, ast.Name)
            and node.value.id == self._df_var
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            return ast.Call(
                func=ast.Attribute(
                    value=ast.Name(id="pl", ctx=ast.Load()),
                    attr="col",
                    ctx=ast.Load(),
                ),
                args=[ast.Constant(value=node.slice.value)],
                keywords=[],
            )
        return node


_PANDAS_ONLY_METHODS = frozenset({
    "fillna", "sort_values", "groupby", "merge", "apply", "pivot_table",
    "reset_index", "drop_duplicates", "astype", "isin", "isna", "notna",
    "append", "iterrows",
    # Note: to_datetime and to_frame are excluded because they also appear
    # as valid polars chained methods (.str.to_datetime, Series.to_frame).
    # pd.to_datetime() is already caught by the pd-alias check.
})


def _has_source_library_calls(node: ast.AST) -> bool:
    """Return True if node contains pandas references that would survive incorrectly.

    Catches both explicit aliases (pd.xxx) and bare pandas-only method names
    that the LLM has not yet converted (e.g. .fillna(), .sort_values()).
    """
    library_aliases = {"pd", "pandas"}
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute):
            if isinstance(child.value, ast.Name) and child.value.id in library_aliases:
                return True
            if child.attr in _PANDAS_ONLY_METHODS:
                return True
        if isinstance(child, ast.Name) and child.id in library_aliases:
            return True
    return False


# ---------------------------------------------------------------------------
# Pass 2: remove .reset_index(drop=True) from expression chains
# ---------------------------------------------------------------------------

def _pass_remove_reset_index(source: str) -> TransformResult:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return TransformResult(code=source, skipped=["syntax_error"])

    source_lines = source.splitlines(keepends=True)
    replacements: list[tuple[int, int, str]] = []
    applied: list[str] = []

    for node in ast.walk(tree):
        call = _find_reset_index_call(node)
        if call is None:
            continue
        receiver = call.func.value  # type: ignore[union-attr]
        stmt = _containing_stmt(tree, call)
        if stmt is None:
            continue
        new_stmt = _replace_expr_in_stmt(stmt, call, receiver)
        if new_stmt is None:
            continue
        stmt_indent = _stmt_indent(stmt, source_lines)
        new_line = stmt_indent + ast.unparse(new_stmt) + "\n"
        start = stmt.lineno - 1
        end = stmt.end_lineno
        replacements.append((start, end, new_line))
        applied.append(f"line {stmt.lineno}: removed .reset_index(drop=True)")

    if not replacements:
        return TransformResult(code=source, applied=applied)

    replacements.sort(key=lambda r: r[0], reverse=True)
    lines = list(source_lines)
    for start, end, new_line in replacements:
        lines[start:end] = [new_line]

    return TransformResult(code="".join(lines), applied=applied)


def _find_reset_index_call(node: ast.AST) -> ast.Call | None:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "reset_index"
    ):
        return None
    # Require drop=True as kwarg or first positional arg
    for kw in node.keywords:
        if kw.arg == "drop" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return node
    if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value is True:
        return node
    return None


# ---------------------------------------------------------------------------
# Pass 3: .sort_values(by, ascending=...) → .sort(by, descending=...)
# ---------------------------------------------------------------------------

def _pass_sort_values(source: str) -> TransformResult:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return TransformResult(code=source, skipped=["syntax_error"])

    source_lines = source.splitlines(keepends=True)
    replacements: list[tuple[int, int, str]] = []
    applied: list[str] = []
    skipped: list[str] = []

    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "sort_values"
        ):
            continue
        stmt = _containing_stmt(tree, node)
        if stmt is None:
            continue

        new_call = _rewrite_sort_values(node, skipped)
        if new_call is None:
            continue

        new_stmt = _replace_expr_in_stmt(stmt, node, new_call)
        if new_stmt is None:
            continue

        stmt_indent = _stmt_indent(stmt, source_lines)
        new_line = stmt_indent + ast.unparse(new_stmt) + "\n"
        start = stmt.lineno - 1
        end = stmt.end_lineno
        replacements.append((start, end, new_line))
        applied.append(f"line {stmt.lineno}: .sort_values() → .sort()")

    if not replacements:
        return TransformResult(code=source, applied=applied, skipped=skipped)

    replacements.sort(key=lambda r: r[0], reverse=True)
    lines = list(source_lines)
    for start, end, new_line in replacements:
        lines[start:end] = [new_line]

    return TransformResult(code="".join(lines), applied=applied, skipped=skipped)


def _rewrite_sort_values(
    call: ast.Call,
    skipped: list[str],
) -> ast.Call | None:
    """Return a new .sort(...) Call node, or None if the pattern is too complex."""
    new_call = copy.deepcopy(call)
    new_call.func.attr = "sort"  # type: ignore[union-attr]

    ascending_kw: ast.keyword | None = None
    for kw in new_call.keywords:
        if kw.arg == "ascending":
            ascending_kw = kw
            break

    if ascending_kw is None:
        # No ascending kwarg — default is ascending, no descending kwarg needed.
        new_call.keywords = [kw for kw in new_call.keywords if kw.arg != "axis"]
        return new_call

    asc_val = ascending_kw.value
    # Single bool: ascending=True → no kwarg needed; ascending=False → descending=True
    if isinstance(asc_val, ast.Constant):
        new_call.keywords = [kw for kw in new_call.keywords if kw.arg not in ("ascending", "axis")]
        if asc_val.value is False:
            new_call.keywords.append(
                ast.keyword(arg="descending", value=ast.Constant(value=True))
            )
        return new_call

    # List of booleans: [True, False, ...] → [False, True, ...]
    if isinstance(asc_val, ast.List) and all(
        isinstance(elt, ast.Constant) and isinstance(elt.value, bool)
        for elt in asc_val.elts
    ):
        inverted = ast.List(
            elts=[ast.Constant(value=not elt.value) for elt in asc_val.elts],  # type: ignore[union-attr]
            ctx=ast.Load(),
        )
        ast.fix_missing_locations(inverted)
        new_call.keywords = [kw for kw in new_call.keywords if kw.arg not in ("ascending", "axis")]
        new_call.keywords.append(ast.keyword(arg="descending", value=inverted))
        return new_call

    skipped.append(
        f"line {call.lineno}: .sort_values() skipped — ascending= value is not a "
        "literal bool or list of bools"
    )
    return None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _stmt_indent(node: ast.AST, source_lines: list[str]) -> str:
    lineno = getattr(node, "lineno", None)
    if lineno is None or lineno > len(source_lines):
        return ""
    line = source_lines[lineno - 1]
    return line[: len(line) - len(line.lstrip())]


def _containing_stmt(tree: ast.Module, target: ast.AST) -> ast.stmt | None:
    """Return the top-level or function-body statement that directly contains *target*."""
    target_id = id(target)

    def _search(stmts: list[ast.stmt]) -> ast.stmt | None:
        for stmt in stmts:
            for child in ast.walk(stmt):
                if id(child) == target_id:
                    return stmt
        return None

    result = _search(tree.body)
    if result is not None:
        return result
    for node in ast.walk(tree):
        for attr in ("body", "orelse", "finalbody"):
            sub = getattr(node, attr, None)
            if isinstance(sub, list):
                result = _search(sub)
                if result is not None:
                    return result
    return None


def _replace_expr_in_stmt(
    stmt: ast.stmt,
    old_expr: ast.expr,
    new_expr: ast.expr,
) -> ast.stmt | None:
    """Return a deep copy of *stmt* with *old_expr* replaced by *new_expr*."""
    old_id = id(old_expr)
    new_stmt = copy.deepcopy(stmt)

    class _Replacer(ast.NodeTransformer):
        def generic_visit(self, node: ast.AST) -> ast.AST:
            if id(node) == old_id:
                return new_expr
            return super().generic_visit(node)

    # Since deepcopy changed all ids, we need to find by structural equality.
    class _ReplacerByUnparse(ast.NodeTransformer):
        _target_str = ast.unparse(old_expr)
        _replaced = False

        def visit(self, node: ast.AST) -> ast.AST:
            if not self._replaced and isinstance(node, ast.expr):
                try:
                    if ast.unparse(node) == self._target_str:
                        self._replaced = True
                        return new_expr
                except Exception:
                    pass
            return super().generic_visit(node)

    replacer = _ReplacerByUnparse()
    result = replacer.visit(new_stmt)
    if not replacer._replaced:
        return None
    ast.fix_missing_locations(result)
    return result
