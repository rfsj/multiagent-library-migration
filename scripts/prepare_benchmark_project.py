from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tools.test_runner import run_pytest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    task_dir = ROOT / "benchmark" / args.task_id
    project_dir = task_dir / "input_project"
    if not project_dir.exists():
        raise FileNotFoundError(f"Benchmark input_project not found: {project_dir}")

    prep_dir = task_dir / "preparation"
    prep_dir.mkdir(parents=True, exist_ok=True)
    before = run_pytest(project_dir, prep_dir / "tests_before_preparation.log")
    proposed_changes = _propose_changes(project_dir, prep_dir / "tests_before_preparation.log")

    applied_changes: list[dict[str, Any]] = []
    if args.apply:
        for change in proposed_changes:
            applied = _apply_change(project_dir, change)
            if applied:
                applied_changes.append(change)

    after = None
    if args.apply:
        after = run_pytest(project_dir, prep_dir / "tests_after_preparation.log")

    report = {
        "task_id": args.task_id,
        "project_dir": str(project_dir),
        "mode": "apply" if args.apply else "dry_run",
        "tests_before": before["status"],
        "tests_after": after["status"] if after else "not_run",
        "proposed_changes": proposed_changes,
        "applied_changes": applied_changes,
        "status": _status(before, after, proposed_changes, args.apply),
        "policy": (
            "Preparation is a separate pre-migration step. It may make the imported "
            "benchmark runnable, but it must not perform library migration."
        ),
    }
    (prep_dir / "preparation_report.json").write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2))
    return 0 if report["status"] in {"already_ready", "prepared", "changes_proposed"} else 1


def _propose_changes(project_dir: Path, pytest_log: Path) -> list[dict[str, Any]]:
    log = pytest_log.read_text(encoding="utf-8")
    changes = []
    pytest_ini_change = _propose_pytest_ini_testpaths_fix(project_dir)
    if pytest_ini_change:
        changes.append(pytest_ini_change)

    missing_config_change = _propose_missing_config_module_fix(project_dir, log)
    if missing_config_change:
        changes.append(missing_config_change)

    missing_fixture_change = _propose_missing_fixture_alias_fix(project_dir, log)
    if missing_fixture_change:
        changes.append(missing_fixture_change)

    return changes


def _propose_pytest_ini_testpaths_fix(project_dir: Path) -> dict[str, Any] | None:
    pytest_ini = project_dir / "pytest.ini"
    tests_dir = project_dir / "tests"
    if not pytest_ini.exists() or not tests_dir.exists():
        return None

    content = pytest_ini.read_text(encoding="utf-8")
    match = re.search(r"(?m)^(testpaths\s*=\s*)test\s*$", content)
    if not match or (project_dir / "test").exists():
        return None

    return {
        "type": "update_pytest_testpaths",
        "file": "pytest.ini",
        "reason": "pytest.ini points to 'test', but the project contains 'tests'.",
        "from": "test",
        "to": "tests",
    }


def _propose_missing_config_module_fix(project_dir: Path, pytest_log: str) -> dict[str, Any] | None:
    match = re.search(r"No module named '([A-Za-z_][A-Za-z0-9_]*)\.config'", pytest_log)
    if not match:
        return None

    package = match.group(1)
    package_dir = project_dir / package
    config_dir = project_dir / "config"
    target = package_dir / "config.py"
    if not package_dir.exists() or target.exists() or not config_dir.exists():
        return None

    tests_reference_load_config = any(
        "load_config" in path.read_text(encoding="utf-8", errors="ignore")
        for path in (project_dir / "tests").rglob("test*.py")
    ) if (project_dir / "tests").exists() else False
    if not tests_reference_load_config:
        return None

    return {
        "type": "add_yaml_config_loader",
        "file": f"{package}/config.py",
        "reason": (
            f"Tests import {package}.config.load_config, but {package}/config.py is missing."
        ),
        "function": "load_config",
    }


def _propose_missing_fixture_alias_fix(project_dir: Path, pytest_log: str) -> dict[str, Any] | None:
    match = re.search(r"fixture '([A-Za-z_][A-Za-z0-9_]*)' not found", pytest_log)
    if not match:
        return None

    missing_fixture = match.group(1)
    conftest = project_dir / "tests" / "conftest.py"
    if not conftest.exists():
        return None

    content = conftest.read_text(encoding="utf-8")
    if re.search(rf"def\s+{re.escape(missing_fixture)}\s*\(", content):
        return None

    source_fixture = None
    if missing_fixture.endswith("_input_df") and re.search(r"def\s+source_df\s*\(", content):
        source_fixture = "source_df"
    if source_fixture is None:
        return None

    return {
        "type": "add_fixture_alias",
        "file": "tests/conftest.py",
        "reason": (
            f"Tests request fixture '{missing_fixture}', and '{source_fixture}' "
            "already provides the input dataframe."
        ),
        "fixture": missing_fixture,
        "source_fixture": source_fixture,
    }


def _apply_change(project_dir: Path, change: dict[str, Any]) -> bool:
    if change["type"] == "update_pytest_testpaths":
        path = project_dir / change["file"]
        content = path.read_text(encoding="utf-8")
        updated = re.sub(r"(?m)^(testpaths\s*=\s*)test\s*$", r"\1tests", content)
        if updated == content:
            return False
        path.write_text(updated, encoding="utf-8")
        return True

    if change["type"] == "add_yaml_config_loader":
        path = project_dir / change["file"]
        path.write_text(_yaml_config_loader_source(), encoding="utf-8")
        return True

    if change["type"] == "add_fixture_alias":
        path = project_dir / change["file"]
        content = path.read_text(encoding="utf-8")
        fixture = change["fixture"]
        source_fixture = change["source_fixture"]
        if re.search(rf"def\s+{re.escape(fixture)}\s*\(", content):
            return False
        suffix = "" if content.endswith("\n") else "\n"
        path.write_text(
            content
            + suffix
            + "\n"
            + "@pytest.fixture()\n"
            + f"def {fixture}({source_fixture}):\n"
            + f"    return {source_fixture}\n",
            encoding="utf-8",
        )
        return True

    return False


def _yaml_config_loader_source() -> str:
    return '''from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_config(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(path)

    with path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    if not isinstance(config, dict):
        raise ValueError("Config file must contain a YAML mapping.")

    for section in ("paths", "schema"):
        if section not in config:
            raise ValueError(f"Missing required config section: {section}")

    return config
'''


def _status(
    before: dict[str, Any],
    after: dict[str, Any] | None,
    proposed_changes: list[dict[str, Any]],
    apply: bool,
) -> str:
    if before["passed"]:
        return "already_ready"
    if not proposed_changes:
        return "no_safe_changes_found"
    if not apply:
        return "changes_proposed"
    if after and after["passed"]:
        return "prepared"
    return "preparation_failed"


if __name__ == "__main__":
    raise SystemExit(main())
