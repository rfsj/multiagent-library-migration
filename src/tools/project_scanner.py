from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

DEPENDENCY_FILES = ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg")
TEST_DIR_NAMES = {"test", "tests"}


def scan_project(
    project_dir: Path,
    source_library: str,
    *,
    use_ast: bool = True,
) -> dict[str, Any]:
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
    source_imports: list[dict[str, Any]] = []
    source_api_calls: list[dict[str, Any]] = []
    production_source_files: list[str] = []

    for path in project_dir.rglob("*.py"):
        if ".venv" in path.parts:
            continue
        rel = str(path.relative_to(project_dir))
        is_test = _is_test_file(path.relative_to(project_dir))
        if not is_test:
            production_source_files.append(rel)
        source = path.read_text(encoding="utf-8")
        if not use_ast:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        aliases = _library_aliases(tree, source_library)
        _scan_ast_tree(
            tree,
            aliases,
            source_library,
            rel,
            is_test,
            source_imports,
            source_api_calls,
        )

    affected_files = sorted(
        {item["file"] for item in source_imports + source_api_calls}
    )
    affected_source_files = sorted(
        {
            item["file"]
            for item in source_imports + source_api_calls
            if not item["is_test"]
        }
    )
    test_files_with_source_library_usage = sorted(
        {item["file"] for item in source_imports + source_api_calls if item["is_test"]}
    )
    source_imports_in_source = [item for item in source_imports if not item["is_test"]]
    source_api_calls_in_source = [
        item for item in source_api_calls if not item["is_test"]
    ]
    source_imports_in_tests = [item for item in source_imports if item["is_test"]]
    source_api_calls_in_tests = [item for item in source_api_calls if item["is_test"]]
    return {
        "dependency_files": sorted(set(dependency_files)),
        "dependency_specs": _dependency_specs(project_dir, dependency_files),
        "test_files": sorted(test_files),
        "production_source_files": sorted(set(production_source_files)),
        "affected_files": affected_files,
        "affected_source_files": affected_source_files,
        "test_files_with_source_library_usage": test_files_with_source_library_usage,
        "source_imports": source_imports,
        "source_api_calls": source_api_calls,
        "source_imports_in_source": source_imports_in_source,
        "source_api_calls_in_source": source_api_calls_in_source,
        "source_imports_in_tests": source_imports_in_tests,
        "source_api_calls_in_tests": source_api_calls_in_tests,
    }


def build_project_audit(
    project_dir: Path,
    source_library: str,
    target_library: str,
    *,
    use_ast: bool = True,
) -> dict[str, Any]:
    scan = scan_project(project_dir, source_library, use_ast=use_ast)
    dependency_summary = _dependency_summary(
        scan["dependency_specs"],
        source_library,
        target_library,
    )
    return {
        "project_dir": str(project_dir),
        "source_library": source_library,
        "target_library": target_library,
        "dependency_files": scan["dependency_files"],
        "dependency_specs": scan["dependency_specs"],
        "dependency_summary": dependency_summary,
        "test_files": scan["test_files"],
        "production_source_files": scan["production_source_files"],
        "affected_files": scan["affected_files"],
        "affected_source_files": scan["affected_source_files"],
        "test_files_with_source_library_usage": scan[
            "test_files_with_source_library_usage"
        ],
        "source_import_count": len(scan["source_imports_in_source"]),
        "source_api_call_count": len(scan["source_api_calls_in_source"]),
        "test_import_count": len(scan["source_imports_in_tests"]),
        "test_api_call_count": len(scan["source_api_calls_in_tests"]),
        "source_imports_in_source": scan["source_imports_in_source"],
        "source_api_calls_in_source": scan["source_api_calls_in_source"],
        "source_imports_in_tests": scan["source_imports_in_tests"],
        "source_api_calls_in_tests": scan["source_api_calls_in_tests"],
        "migration_needed": bool(scan["source_imports_in_source"] or scan["source_api_calls_in_source"]),
        "diagnosis_use_ast": use_ast,
        "diagnosis_static_source_detection": use_ast,
        "test_usage_policy": (
            "Source-library usage in tests is recorded for audit, but tests are not migration targets."
        ),
    }


def _scan_ast_tree(
    tree: ast.AST,
    aliases: set[str],
    source_library: str,
    rel: str,
    is_test: bool,
    source_imports: list[dict[str, Any]],
    source_api_calls: list[dict[str, Any]],
) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == source_library:
                    source_imports.append({
                        "file": rel,
                        "line": node.lineno,
                        "alias": alias.asname,
                        "is_test": is_test,
                    })
        if isinstance(node, ast.ImportFrom):
            if node.module == source_library or (node.module or "").startswith(f"{source_library}."):
                source_imports.append({
                    "file": rel,
                    "line": node.lineno,
                    "alias": None,
                    "is_test": is_test,
                })
        if isinstance(node, ast.Call):
            api = _classify_call(node, aliases)
            if api:
                source_api_calls.append({
                    "file": rel,
                    "line": node.lineno,
                    "api": api,
                    "is_test": is_test,
                })
        if isinstance(node, ast.Subscript):
            api = _classify_subscript(node)
            if api:
                source_api_calls.append({
                    "file": rel,
                    "line": node.lineno,
                    "api": api,
                    "is_test": is_test,
                })


def _library_aliases(tree: ast.AST, library: str) -> set[str]:
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == library:
                    aliases.add(alias.asname or library)
    return aliases


def _is_test_file(rel_path: Path) -> bool:
    return (
        bool(TEST_DIR_NAMES.intersection(rel_path.parts))
        or rel_path.name.startswith("test_")
        or rel_path.name.endswith("_test.py")
        or rel_path.name == "conftest.py"
    )


def _dependency_specs(
    project_dir: Path, dependency_files: list[str]
) -> dict[str, list[dict[str, str]]]:
    specs: dict[str, list[dict[str, str]]] = {}
    for rel_path in dependency_files:
        path = project_dir / rel_path
        if path.name == "requirements.txt":
            specs[rel_path] = _requirements_specs(path)
        else:
            specs[rel_path] = []
    return specs


def _requirements_specs(path: Path) -> list[dict[str, str]]:
    specs = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        match = re.match(r"(?P<name>[A-Za-z0-9_.-]+)\s*(?P<constraint>.*)", line)
        if not match:
            continue
        specs.append(
            {
                "name": match.group("name"),
                "constraint": match.group("constraint").strip(),
                "raw": line,
            }
        )
    return specs


def _dependency_summary(
    dependency_specs: dict[str, list[dict[str, str]]],
    source_library: str,
    target_library: str,
) -> dict[str, Any]:
    source_specs = _find_dependency_specs(dependency_specs, source_library)
    target_specs = _find_dependency_specs(dependency_specs, target_library)
    return {
        "source_library": source_library,
        "target_library": target_library,
        "source_dependency_present": bool(source_specs),
        "target_dependency_present": bool(target_specs),
        "source_dependency_specs": source_specs,
        "target_dependency_specs": target_specs,
        "target_dependency_action": "preserve_existing"
        if target_specs
        else "add_dependency",
    }


def _find_dependency_specs(
    dependency_specs: dict[str, list[dict[str, str]]],
    library: str,
) -> list[dict[str, str]]:
    matches = []
    normalized_library = _normalize_package_name(library)
    for file, specs in dependency_specs.items():
        for spec in specs:
            if _normalize_package_name(spec["name"]) == normalized_library:
                matches.append({"file": file, **spec})
    return matches


def _normalize_package_name(name: str) -> str:
    return name.replace("_", "-").lower()


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
