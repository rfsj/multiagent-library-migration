from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True)
class PatternHit:
    line: int
    pattern_id: str
    guidance: str


def scan_for_confusing_patterns(
    source: str,
    source_library: str,
    allowed_symbols: list[str] | None = None,
) -> list[PatternHit]:
    """Walk the AST and return patterns known to be migrated incorrectly.

    If *allowed_symbols* is given, only nodes inside those top-level
    function/class bodies are inspected.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    scoped_functions: list[ast.FunctionDef | ast.AsyncFunctionDef] | None = None
    allowed_nodes: set[int] | None = None
    if allowed_symbols:
        symbol_set = set(allowed_symbols)
        scoped_functions = []
        allowed_nodes = set()
        for node in tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name in symbol_set
            ):
                for child in ast.walk(node):
                    allowed_nodes.add(id(child))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    scoped_functions.append(node)

    hits: list[PatternHit] = []
    for node in ast.walk(tree):
        if allowed_nodes is not None and id(node) not in allowed_nodes:
            continue
        lineno = getattr(node, "lineno", None)
        if lineno is None:
            continue
        hit = _match(node)
        if hit is not None:
            hits.append(PatternHit(line=lineno, pattern_id=hit[0], guidance=hit[1]))

    # Detect sequential column assignments where one references the previous.
    functions_to_scan = scoped_functions if scoped_functions is not None else [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    hits.extend(_detect_dependent_assignments(functions_to_scan))

    seen: set[tuple[int, str]] = set()
    unique: list[PatternHit] = []
    for h in sorted(hits, key=lambda h: h.line):
        key = (h.line, h.pattern_id)
        if key not in seen:
            seen.add(key)
            unique.append(h)
    return unique


def format_pattern_analysis(hits: list[PatternHit]) -> str:
    """Return a prompt section listing detected patterns, or empty string."""
    if not hits:
        return ""
    lines = [
        "## MANDATORY transformations — convert EVERY item below before returning code",
        "## Returning code that still contains any of these patterns is INCORRECT",
    ]
    for hit in hits:
        lines.append(f"- [ ] Line {hit.line}: {hit.guidance}")
    lines.append(
        "## Before finalizing: re-read the migrated code line by line and confirm "
        "each listed line has been rewritten using the target-library equivalent."
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Internal matching logic
# ---------------------------------------------------------------------------

def _detect_dependent_assignments(
    functions: list[ast.FunctionDef | ast.AsyncFunctionDef],
) -> list[PatternHit]:
    """Detect sequential df["col"] assignments where a later one references an
    earlier-created column on the same variable.

    Example that triggers this:
        df["gross_revenue"] = df["qty"] * df["price"]   # line N
        df["net_revenue"]   = df["gross_revenue"] * x   # line N+1 — depends on gross_revenue

    In Polars, both cannot be in the same with_columns() call because
    gross_revenue does not exist yet when net_revenue is evaluated.
    """
    hits: list[PatternHit] = []
    for func in functions:
        # Collect top-level subscript assignments: (lineno, var, col_name)
        assigns: list[tuple[int, str, str]] = []
        for stmt in func.body:
            if not (
                isinstance(stmt, ast.Assign)
                and stmt.targets
                and isinstance(stmt.targets[0], ast.Subscript)
                and isinstance(stmt.targets[0].value, ast.Name)
            ):
                continue
            slice_node = stmt.targets[0].slice
            col = (
                slice_node.value
                if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str)
                else None
            )
            if col is None:
                continue
            assigns.append((stmt.lineno, stmt.targets[0].value.id, col))

        # For each assignment, check whether its value reads a column created
        # by any earlier assignment on the same variable.
        created: dict[tuple[str, str], int] = {}  # (var, col) -> lineno
        for stmt in func.body:
            if not (
                isinstance(stmt, ast.Assign)
                and stmt.targets
                and isinstance(stmt.targets[0], ast.Subscript)
                and isinstance(stmt.targets[0].value, ast.Name)
            ):
                continue
            slice_node = stmt.targets[0].slice
            col = (
                slice_node.value
                if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str)
                else None
            )
            if col is None:
                continue
            var = stmt.targets[0].value.id

            # Check whether stmt.value references any previously created (var, col)
            for sub in ast.walk(stmt.value):
                if not (
                    isinstance(sub, ast.Subscript)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == var
                ):
                    continue
                ref_col = (
                    sub.slice.value
                    if isinstance(sub.slice, ast.Constant) and isinstance(sub.slice.value, str)
                    else None
                )
                if ref_col is None:
                    continue
                created_lineno = created.get((var, ref_col))
                if created_lineno is not None:
                    hits.append(PatternHit(
                        line=stmt.lineno,
                        pattern_id="dependent_column_assign",
                        guidance=(
                            f'df["{col}"] references df["{ref_col}"] created on '
                            f"line {created_lineno}. These CANNOT go in the same "
                            f"with_columns() call — split into two sequential calls: "
                            f"first with_columns([...alias('{ref_col}')]), "
                            f"then with_columns([...alias('{col}')])."
                        ),
                    ))

            # Register this column as created for subsequent statements
            created[(var, col)] = stmt.lineno

    return hits


def _match(node: ast.AST) -> tuple[str, str] | None:
    """Return (pattern_id, guidance) if node matches a known confusing pattern."""

    # Call nodes: method-based patterns
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        attr = node.func.attr

        if attr == "apply":
            if node.args and isinstance(node.args[0], ast.Lambda):
                return (
                    "apply_lambda",
                    ".apply(lambda) → use pl.when(cond).then(a).otherwise(b) for "
                    "row-wise conditionals; or .map_elements(fn, return_dtype=pl.Type) "
                    "for non-vectorizable custom functions",
                )

        elif attr == "pivot_table":
            # Check if aggfunc="nunique" is used
            aggfunc_kw = next(
                (kw for kw in node.keywords if kw.arg == "aggfunc"),
                None,
            )
            aggfunc_val = (
                aggfunc_kw.value.value
                if aggfunc_kw and isinstance(aggfunc_kw.value, ast.Constant)
                else None
            )
            if aggfunc_val == "nunique":
                return (
                    "pivot_table_nunique",
                    "pd.pivot_table(aggfunc='nunique') is NOT directly supported by Polars pivot. "
                    "Pre-aggregate first: group_by([index_col, on_col]).agg(pl.col(values).n_unique().alias('n')), "
                    "then pivot(values='n', index=index_col, on=on_col, aggregate_function='first').fill_null(0). "
                    "CRITICAL: Polars pivot ALWAYS produces STRING column names even when the on-column contains integers. "
                    "If tests check column names or dict keys as integers (e.g. {0: 2, 1: 0}), "
                    "this cannot be satisfied in pure Polars — add to unmigrated_patterns and flag for manual review.",
                )
            return (
                "pivot_table",
                "pd.pivot_table() → .pivot(on=col, aggregate_function='sum').fill_null(0); "
                "then .select([index_cols] + sorted(value_cols)) to preserve column order; "
                "filter null index values before pivoting if the index column is nullable. "
                "CRITICAL: Polars pivot ALWAYS produces STRING column names even when the on-column contains integers. "
                "If tests check column names or dict keys as integers, add to unmigrated_patterns.",
            )

        elif attr == "reset_index":
            return (
                "reset_index",
                ".reset_index(drop=True) → DELETE this call; "
                "Polars DataFrames have no row index",
            )

        elif attr == "sort_values":
            return (
                "sort_values",
                ".sort_values(ascending=[True, False]) → .sort(descending=[False, True]) "
                "with every bool inverted; add nulls_last=True when the column may contain nulls",
            )

        elif attr == "drop_duplicates":
            return (
                "drop_duplicates",
                ".drop_duplicates(subset=cols, keep='first') after sort → "
                ".unique(subset=cols, keep='first', maintain_order=True)",
            )

        elif attr == "groupby":
            return (
                "groupby",
                ".groupby(col, as_index=False).agg(...) → "
                ".group_by(col).agg([pl.col(c).agg_fn().alias(name), ...]); "
                "no as_index parameter; use .n_unique() not deprecated pl.count() for nunique",
            )

        elif attr == "merge":
            return (
                "merge",
                ".merge(other, on=col, how='left') → .join(other, on=col, how='left'); "
                "how='outer' becomes how='full'",
            )

        elif attr == "merge_asof":
            return (
                "merge_asof",
                "pd.merge_asof(left, right, on='ts', by='grp', direction='backward', tolerance=pd.Timedelta(days=N)) → "
                "left.join_asof(right, on='ts', by='grp', strategy='backward', tolerance=timedelta(days=N)); "
                "REQUIRED: sort BOTH DataFrames by the on-column before calling join_asof — unsorted data causes ComputeError; "
                "add 'from datetime import timedelta' at the top; "
                "direction='backward'→strategy='backward', direction='forward'→strategy='forward'; "
                "pd.Timedelta(days=N)→timedelta(days=N) (stdlib timedelta, NOT polars duration)",
            )

        elif attr == "resample":
            return (
                "resample",
                ".groupby(grp).resample('D').agg(close=('price','last'), vol=('v','sum')) → "
                "ticks.sort(['timestamp', grp]).group_by_dynamic('timestamp', every='1d', group_by=grp)"
                ".agg([pl.col('price').last().alias('close'), pl.col('v').sum().alias('vol')]); "
                "REQUIRED: sort by timestamp (and group column) BEFORE group_by_dynamic or you get ComputeError; "
                "CRITICAL: group_by_dynamic does NOT fill missing dates — pandas resample fills gaps with NaN rows. "
                "To replicate gap-filling: (1) build a date grid (pl.date_range + symbols cross join), "
                "(2) left-join the aggregated data onto the grid, "
                "(3) forward-fill with pl.col('close').forward_fill().over(grp); "
                "output column order may differ — use .select([grp, 'timestamp', ...]) to fix",
            )

        elif attr == "pct_change":
            return (
                "pct_change",
                ".pct_change() inside groupby → pl.col('x').pct_change().over('group_col').fill_null(0.0); "
                "standalone: pl.col('x').pct_change().fill_null(0.0)",
            )

        elif attr == "to_datetime":
            return (
                "to_datetime",
                "pd.to_datetime(col, errors='coerce') → "
                "pl.col(name).str.to_date(strict=False) or .str.to_datetime(strict=False); "
                "add nulls_last=True to any subsequent sort on this column",
            )

        elif attr == "fillna":
            method_kw = next((kw for kw in node.keywords if kw.arg == "method"), None)
            if method_kw and isinstance(method_kw.value, ast.Constant):
                method_val = method_kw.value.value
                if method_val == "ffill":
                    return (
                        "fillna_ffill",
                        ".fillna(method='ffill') → .forward_fill(); "
                        "inside a groupby context use pl.col('x').forward_fill().over('group_col')",
                    )
                if method_val == "bfill":
                    return (
                        "fillna_bfill",
                        ".fillna(method='bfill') → .backward_fill(); "
                        "inside a groupby context use pl.col('x').backward_fill().over('group_col')",
                    )
            return (
                "fillna",
                ".fillna(v) → .fill_null(v)",
            )

        elif attr in ("isna", "notna"):
            return (
                "isna_notna",
                ".isna() → .is_null(); .notna() → .is_not_null()",
            )

        elif attr == "astype":
            return (
                "astype",
                ".astype(int) → .cast(pl.Int64); "
                ".astype(float) → .cast(pl.Float64); "
                ".astype(str) → .cast(pl.String)",
            )

        elif attr == "isin":
            return (
                "isin",
                ".isin([...]) → .is_in([...]); "
                "if negated (~.isin([...])) use .is_in([...]).not_()",
            )

        elif attr == "copy":
            return (
                "copy_call",
                ".copy() → DELETE this call; Polars DataFrames are immutable, "
                ".copy() has no equivalent and must be removed",
            )

        elif attr == "strip" and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "str":
            return (
                "str_strip",
                ".str.strip() → .str.strip_chars(); "
                "Polars has NO str.strip() — it does NOT exist; use .str.strip_chars() instead",
            )

        elif attr == "contains" and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "str":
            # Check if case=False is passed (case-insensitive contains)
            has_case_false = any(
                kw.arg == "case"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is False
                for kw in node.keywords
            )
            if has_case_false:
                return (
                    "str_contains_case_insensitive",
                    ".str.contains(keyword, case=False) → "
                    "pl.col(x).str.to_lowercase().str.contains(keyword.lower()); "
                    "Polars str.contains() has NO case= parameter — it does NOT exist; "
                    "never use case=True or case_insensitive=True",
                )

    # ~expr.isin(...) — explicit negated isin check
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.Invert)
        and isinstance(node.operand, ast.Call)
        and isinstance(node.operand.func, ast.Attribute)
        and node.operand.func.attr == "isin"
    ):
        return (
            "isin_negated",
            "~expr.isin([...]) → pl.col(...).is_in([...]).not_()",
        )

    # df[mask] — boolean indexing for row filtering
    # Column selection (df["col"] or df[["c1","c2"]]) is intentionally excluded.
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        s = node.slice
        is_string_col = isinstance(s, ast.Constant) and isinstance(s.value, str)
        is_col_list = isinstance(s, ast.List) and all(
            isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            for elt in s.elts
        )
        if not is_string_col and not is_col_list:
            return (
                "boolean_indexing",
                "df[mask] → df.filter(polars_expr); "
                "NEVER write df[pl.col('x') == val] — Polars raises TypeError at runtime; "
                "use df.filter(pl.col('x') == val) instead",
            )

    # df["col"] = expr — index assignment to a DataFrame variable
    if (
        isinstance(node, ast.Assign)
        and node.targets
        and isinstance(node.targets[0], ast.Subscript)
        and isinstance(node.targets[0].value, ast.Name)
    ):
        return (
            "column_assign",
            'df["col"] = expr → df = df.with_columns(expr.alias("col")); '
            "never assign to a Polars DataFrame by subscript index",
        )

    return None
