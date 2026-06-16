"""Generic, library-parameterized detection of source-library API usage.

Unlike `pattern_scanner` — a hand-curated pandas→polars catalog *with guidance* used
as the prompt/rescan intervention — this module only *detects* source-library usage,
deriving its surface from the libraries themselves by introspection. No guidance, no
hand-written pattern list: a method name counts as "source-specific" when it exists on
the source library but not on the target (so it cannot be a successful conversion).

It powers measurement that must stay library-agnostic and catalog-free (mine_failures;
optionally the validation source-usage count). See ai_docs/proposal-failure-mining.md.
"""

from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module

# Short aliases per library, so code that dropped the import but kept ``pd.read_csv``
# is still attributed to the source library.
_COMMON_ALIASES: dict[str, set[str]] = {
    "pandas": {"pd", "pandas"},
    "polars": {"pl", "polars"},
    "numpy": {"np", "numpy"},
}


@dataclass(frozen=True)
class ApiHit:
    line: int
    name: str
    kind: str  # "attr" (alias-qualified), "call" (source-specific method), "structural"


# Accessor namespaces shared by both libraries (df.dt.X, s.str.X, ...). Methods called
# *through* one of these are ambiguous — both libs expose the namespace — so we do not
# treat them as source-specific. This removes the dt/str false positives generically.
_NAMESPACE_ACCESSORS = frozenset(
    {"dt", "str", "list", "cat", "struct", "arr", "array", "sparse", "plot"}
)

# Names both libraries expose but on the GroupBy surface (df.groupby().agg(...) and
# df.group_by().agg(...)), which the namespace rule above does not cover. Tiny,
# documented measurement refinement — NOT guidance, extendable per library pair.
_AMBIGUOUS_SHARED: dict[tuple[str, str], frozenset[str]] = {
    ("pandas", "polars"): frozenset({"agg"}),
}


@lru_cache(maxsize=None)
def source_api_surface(library: str) -> frozenset[str]:
    """Public names exposed by the library: module-level names + the public methods of
    every class exposed at the top level (DataFrame, Series, Expr, LazyFrame, ...).
    Derived by importing the library — no hand-maintained list."""
    try:
        mod = import_module(library)
    except ImportError:
        return frozenset()
    names = {n for n in dir(mod) if not n.startswith("_")}
    for attr in dir(mod):
        if attr.startswith("_"):
            continue
        obj = getattr(mod, attr, None)
        if isinstance(obj, type):
            names |= {n for n in dir(obj) if not n.startswith("_")}
    return frozenset(names)


@lru_cache(maxsize=1)
def _generic_names() -> frozenset[str]:
    """Names shared with builtin containers/objects — excluded to avoid flagging
    generic ``.get``/``.items``/``.copy`` style calls as source-library usage."""
    names = set(dir(builtins))
    for tp in (dict, list, str, set, tuple, frozenset, bytes, int, float, object):
        names |= {n for n in dir(tp) if not n.startswith("_")}
    return frozenset(names)


@lru_cache(maxsize=None)
def source_specific_names(
    source_library: str, target_library: str = "polars"
) -> frozenset[str]:
    """Method/function names that exist on the source library but NOT on the target —
    i.e. their presence in migrated code means an unconverted source-ism."""
    return frozenset(
        source_api_surface(source_library)
        - source_api_surface(target_library)
        - _generic_names()
        - _AMBIGUOUS_SHARED.get((source_library, target_library), frozenset())
    )


def _aliases(tree: ast.Module, source_library: str) -> set[str]:
    aliases = set(_COMMON_ALIASES.get(source_library, {source_library}))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == source_library or alias.name.startswith(
                    f"{source_library}."
                ):
                    aliases.add(alias.asname or alias.name.split(".")[0])
    return aliases


def detect_source_api(
    code: str,
    source_library: str,
    target_library: str = "polars",
) -> list[ApiHit]:
    """Return source-library usages still present in *code*:

    - ``<alias>.<name>`` (e.g. ``pd.read_csv``) — any attribute on the source alias;
    - ``x.<name>(...)`` where ``<name>`` is source-specific (in source, not in target);
    - structural idioms that carry no method name: boolean indexing ``df[mask]`` and
      subscript column assignment ``df["c"] = ...``.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    aliases = _aliases(tree, source_library)
    specific = source_specific_names(source_library, target_library)
    hits: list[ApiHit] = []

    for node in ast.walk(tree):
        # alias-qualified attribute: pd.read_csv, pd.DataFrame, pd.concat, ...
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in aliases
        ):
            hits.append(ApiHit(node.lineno, node.attr, "attr"))
        # unqualified source-specific method call: df.sort_values(...), s.fillna(...).
        # Skip calls through a shared accessor namespace (df.dt.X, s.str.X) — both
        # libraries expose those, so the name is not a reliable source-ism.
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in specific
            and not (
                isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr in _NAMESPACE_ACCESSORS
            )
        ):
            hits.append(ApiHit(node.func.lineno, node.func.attr, "call"))

    hits.extend(_structural_hits(tree))
    return _dedupe(hits)


def _structural_hits(tree: ast.Module) -> list[ApiHit]:
    hits: list[ApiHit] = []
    for node in ast.walk(tree):
        # boolean_indexing: df[<non-column-selector>] (column selection is excluded)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            s = node.slice
            is_col = isinstance(s, ast.Constant) and isinstance(s.value, str)
            is_col_list = (
                isinstance(s, ast.List)
                and bool(s.elts)
                and all(
                    isinstance(e, ast.Constant) and isinstance(e.value, str)
                    for e in s.elts
                )
            )
            if not is_col and not is_col_list:
                hits.append(ApiHit(node.lineno, "boolean_indexing", "structural"))
        # column_assign: df[...] = ...
        if (
            isinstance(node, ast.Assign)
            and node.targets
            and isinstance(node.targets[0], ast.Subscript)
            and isinstance(node.targets[0].value, ast.Name)
        ):
            hits.append(ApiHit(node.targets[0].lineno, "column_assign", "structural"))
    return hits


def _dedupe(hits: list[ApiHit]) -> list[ApiHit]:
    seen: set[tuple[int, str]] = set()
    unique: list[ApiHit] = []
    for hit in sorted(hits, key=lambda h: (h.line, h.name)):
        key = (hit.line, hit.name)
        if key not in seen:
            seen.add(key)
            unique.append(hit)
    return unique
