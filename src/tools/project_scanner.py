from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

DEPENDENCY_FILES = ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg")


def scan_project(project_dir: Path) -> dict[str, Any]:
    dependency_files = [
        str(path.relative_to(project_dir))
        for name in DEPENDENCY_FILES
        for path in project_dir.rglob(name)
        if ".venv" not in path.parts
    ]
    test_files = [
        str(path.relative_to(project_dir))
        for path in project_dir.rglob("test*.py")
        if ".venv" not in path.parts
    ]
    pandas_imports: list[dict[str, Any]] = []
    pandas_api_calls: list[dict[str, Any]] = []

    for path in project_dir.rglob("*.py"):
        if ".venv" in path.parts:
            continue
        rel = str(path.relative_to(project_dir))
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        aliases = _pandas_aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "pandas":
                        pandas_imports.append({"file": rel, "line": node.lineno, "alias": alias.asname})
            if isinstance(node, ast.Call):
                api = _classify_call(node, aliases)
                if api:
                    pandas_api_calls.append({"file": rel, "line": node.lineno, "api": api})
            if isinstance(node, ast.Subscript):
                api = _classify_subscript(node)
                if api:
                    pandas_api_calls.append({"file": rel, "line": node.lineno, "api": api})

    affected_files = sorted({item["file"] for item in pandas_imports + pandas_api_calls})
    return {
        "dependency_files": sorted(set(dependency_files)),
        "test_files": sorted(test_files),
        "affected_files": affected_files,
        "pandas_imports": pandas_imports,
        "pandas_api_calls": pandas_api_calls,
    }


def _pandas_aliases(tree: ast.AST) -> set[str]:
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pandas":
                    aliases.add(alias.asname or "pandas")
    return aliases


def _classify_call(node: ast.Call, aliases: set[str]) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name) and func.value.id in aliases:
            return f"{func.value.id}.{func.attr}"
        if func.attr == "sort_values":
            return "sort_values"
    return None


def _classify_subscript(node: ast.Subscript) -> str | None:
    if isinstance(node.slice, ast.Compare):
        return "boolean_filter"
    if isinstance(node.slice, ast.List):
        return "column_selection"
    return None
