from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tools.test_runner import run_pytest


# ---------------------------------------------------------------------------
# Known module-name → pip-package-name mismatches
# ---------------------------------------------------------------------------
_MODULE_TO_PKG: dict[str, str] = {
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "skimage": "scikit-image",
    "Crypto": "pycryptodome",
    "pkg_resources": "setuptools",
    "parameterized": "parameterized",
}

# ---------------------------------------------------------------------------
# Removed / renamed pandas 2.x APIs detected by name in the pytest output,
# with ready-to-paste conftest.py shims.
# ---------------------------------------------------------------------------
_PANDAS_COMPAT_PATCHES: dict[str, str] = {
    "check_less_precise": """\
import pandas as _pd

_orig_ase = _pd.testing.assert_series_equal
_orig_afe = _pd.testing.assert_frame_equal


def _ase(*args, check_less_precise=None, **kwargs):
    if check_less_precise is not None:
        kwargs.setdefault("rtol", 1.5e-3)
    return _orig_ase(*args, **kwargs)


def _afe(*args, check_less_precise=None, **kwargs):
    if check_less_precise is not None:
        kwargs.setdefault("rtol", 1.5e-3)
    return _orig_afe(*args, **kwargs)


_pd.testing.assert_series_equal = _ase
_pd.testing.assert_frame_equal = _afe
""",
    "convert_dtype": """\
import pandas as _pd

_orig_read_csv = _pd.read_csv


def _patched_read_csv(*args, convert_dtype=None, **kwargs):
    return _orig_read_csv(*args, **kwargs)


_pd.read_csv = _patched_read_csv
""",
}


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

    # Always install declared requirements first so import errors reflect
    # genuinely missing packages, not simply "project not yet installed".
    _install_requirements(project_dir)

    before = run_pytest(project_dir, prep_dir / "tests_before_preparation.log")
    proposed_changes = _propose_changes(project_dir, prep_dir / "tests_before_preparation.log")

    applied_changes: list[dict[str, Any]] = []
    if args.apply:
        for change in proposed_changes:
            applied = _apply_change(project_dir, change)
            if applied:
                applied_changes.append(change)

        # Install any newly added requirements before re-running tests
        if applied_changes:
            _install_requirements(project_dir)

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


# ---------------------------------------------------------------------------
# Propose changes
# ---------------------------------------------------------------------------

def _propose_changes(project_dir: Path, pytest_log: Path) -> list[dict[str, Any]]:
    log = pytest_log.read_text(encoding="utf-8")
    changes = []

    for fn in (
        _propose_pytest_ini_testpaths_fix,
        _propose_missing_config_module_fix,
        _propose_missing_fixture_alias_fix,
        _propose_missing_dep_fix,
        _propose_pandas_compat_fix,
    ):
        result = fn(project_dir, log)
        if result:
            changes.append(result)

    return changes


def _propose_pytest_ini_testpaths_fix(project_dir: Path, log: str) -> dict[str, Any] | None:
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


def _propose_missing_config_module_fix(project_dir: Path, log: str) -> dict[str, Any] | None:
    match = re.search(r"No module named '([A-Za-z_][A-Za-z0-9_]*)\.config'", log)
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


def _propose_missing_fixture_alias_fix(project_dir: Path, log: str) -> dict[str, Any] | None:
    match = re.search(r"fixture '([A-Za-z_][A-Za-z0-9_]*)' not found", log)
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


def _propose_missing_dep_fix(project_dir: Path, log: str) -> dict[str, Any] | None:
    """Add packages to requirements.txt for every ModuleNotFoundError in the test log."""
    raw = re.findall(r"No module named '([A-Za-z][A-Za-z0-9_.]*)'", log)
    if not raw:
        return None

    top_level = {m.split(".")[0] for m in raw}

    # Own packages present in the project root (should be installed via -e .)
    own_packages = {
        d.name for d in project_dir.iterdir()
        if d.is_dir() and (d / "__init__.py").exists()
    }

    req_path = project_dir / "requirements.txt"
    existing = req_path.read_text(encoding="utf-8") if req_path.exists() else ""
    existing_lower = existing.lower()

    to_add: list[str] = []

    if top_level & own_packages and "-e ." not in existing:
        to_add.append("-e .")

    for mod in sorted(top_level - own_packages):
        pkg = _MODULE_TO_PKG.get(mod, mod)
        if pkg.lower() not in existing_lower and mod.lower() not in existing_lower:
            to_add.append(pkg)

    if not to_add:
        return None

    return {
        "type": "add_requirements",
        "file": "requirements.txt",
        "packages": to_add,
        "reason": (
            f"Tests fail with ModuleNotFoundError. Adding missing packages: "
            f"{', '.join(to_add)}"
        ),
    }


def _propose_pandas_compat_fix(project_dir: Path, log: str) -> dict[str, Any] | None:
    """Create a conftest.py shim for pandas 2.x APIs removed/renamed from test assertions."""
    detected = [key for key in _PANDAS_COMPAT_PATCHES if key in log]
    if not detected:
        return None

    conftest = project_dir / "conftest.py"
    existing = conftest.read_text(encoding="utf-8") if conftest.exists() else ""
    needed = [k for k in detected if k not in existing]
    if not needed:
        return None

    return {
        "type": "add_pandas_compat_conftest",
        "file": "conftest.py",
        "apis": needed,
        "reason": (
            f"Tests use pandas API removed in 2.x: {', '.join(needed)}. "
            "Adding conftest.py compatibility shim."
        ),
    }


# ---------------------------------------------------------------------------
# Apply changes
# ---------------------------------------------------------------------------

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

    if change["type"] == "add_requirements":
        req_path = project_dir / change["file"]
        existing = req_path.read_text(encoding="utf-8") if req_path.exists() else ""
        existing_lower = existing.lower()
        to_add = [
            p for p in change["packages"]
            if p.lower() not in existing_lower
        ]
        if not to_add:
            return False
        suffix = "" if not existing or existing.endswith("\n") else "\n"
        req_path.write_text(existing + suffix + "\n".join(to_add) + "\n", encoding="utf-8")
        return True

    if change["type"] == "add_pandas_compat_conftest":
        conftest = project_dir / change["file"]
        existing = conftest.read_text(encoding="utf-8") if conftest.exists() else ""
        patches = "\n".join(_PANDAS_COMPAT_PATCHES[api] for api in change["apis"])
        sep = "\n" if existing and not existing.endswith("\n\n") else ""
        conftest.write_text(existing + sep + patches, encoding="utf-8")
        return True

    return False


def _install_requirements(project_dir: Path) -> None:
    """Run pip install -r requirements.txt from within the project directory."""
    req = project_dir / "requirements.txt"
    if not req.exists():
        return
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"],
        cwd=project_dir,
        check=False,
    )


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------

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
