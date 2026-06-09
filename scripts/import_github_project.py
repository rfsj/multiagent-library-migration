from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP_DIR = ROOT / "benchmark" / "_imports"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("repo_url")
    parser.add_argument("--source-library", default="pandas")
    parser.add_argument("--target-library", default="polars")
    parser.add_argument("--branch", default=None)
    parser.add_argument("--description", default=None)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    task_dir = ROOT / "benchmark" / args.task_id
    input_project = task_dir / "input_project"
    clone_dir = TMP_DIR / args.task_id

    if task_dir.exists() and not args.overwrite:
        raise FileExistsError(
            f"Benchmark task already exists: {task_dir}. Use --overwrite to replace it."
        )

    if task_dir.exists():
        shutil.rmtree(task_dir)
    if clone_dir.exists():
        shutil.rmtree(clone_dir)
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    clone_cmd = ["git", "clone", "--depth", "1"]
    if args.branch:
        clone_cmd.extend(["--branch", args.branch])
    clone_cmd.extend([args.repo_url, str(clone_dir)])
    subprocess.run(clone_cmd, check=True)

    ignore = shutil.ignore_patterns(
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        "node_modules",
    )
    shutil.copytree(clone_dir, input_project, ignore=ignore)
    shutil.rmtree(clone_dir)

    _bootstrap_project(input_project, args.source_library)

    metadata = {
        "task_id": args.task_id,
        "source_library": args.source_library,
        "target_library": args.target_library,
        "description": args.description
        or f"GitHub project benchmark imported from {args.repo_url}.",
        "source_repo": args.repo_url,
        "source_branch": args.branch,
        "expected_changed_files": [],
    }
    (task_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps({
        "task_id": args.task_id,
        "source_repo": args.repo_url,
        "task_dir": str(task_dir),
        "input_project": str(input_project),
        "metadata": str(task_dir / "metadata.json"),
    }, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Bootstrap helpers
# ---------------------------------------------------------------------------

def _bootstrap_project(project_dir: Path, source_lib: str) -> None:
    """Ensure the imported project has a minimal requirements.txt and pytest config."""
    _bootstrap_requirements(project_dir, source_lib)
    _bootstrap_pytest_ini(project_dir)


def _bootstrap_requirements(project_dir: Path, source_lib: str) -> None:
    """Ensure requirements.txt exists and contains the essentials for the benchmark."""
    req = project_dir / "requirements.txt"
    essentials = ["-e .", f"{source_lib}==2.2.3", "pytest==8.3.4"]

    if not req.exists():
        req.write_text("\n".join(essentials) + "\n", encoding="utf-8")
        return

    content = req.read_text(encoding="utf-8")
    # If the file only aggregates other requirements files (dev-tool includes),
    # it's not suitable as a benchmark driver — replace with a minimal one.
    real_lines = [
        ln.strip() for ln in content.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if real_lines and all(ln.startswith("-r ") for ln in real_lines):
        req.write_text("\n".join(essentials) + "\n", encoding="utf-8")
        return

    # Otherwise keep it, but append any missing essentials
    content_lower = content.lower()
    to_add = [e for e in essentials if e.split("==")[0].lower() not in content_lower]
    if to_add:
        suffix = "" if content.endswith("\n") else "\n"
        req.write_text(content + suffix + "\n".join(to_add) + "\n", encoding="utf-8")


def _bootstrap_pytest_ini(project_dir: Path) -> None:
    """Create pytest.ini if no pytest config exists, handling non-standard test layouts."""
    # Skip if any recognised pytest config already present
    for name in ("pytest.ini", "tox.ini"):
        if (project_dir / name).exists():
            return
    for name in ("pyproject.toml", "setup.cfg"):
        cfg = project_dir / name
        if cfg.exists():
            content = cfg.read_text(encoding="utf-8")
            if "[tool.pytest" in content or "[pytest]" in content:
                return

    # Locate test root directory
    test_root: Path | None = None
    for candidate in ("tests", "test"):
        d = project_dir / candidate
        if d.is_dir():
            test_root = d
            break
    if test_root is None:
        return

    py_files = [
        f for f in test_root.rglob("*.py")
        if f.name not in ("__init__.py", "conftest.py")
    ]
    if not py_files:
        return

    has_standard_names = any(f.name.startswith("test_") for f in py_files)
    rel_root = test_root.name  # "tests" or "test"

    if has_standard_names:
        ini = f"[pytest]\ntestpaths = {rel_root}\n"
    else:
        # Non-standard naming: identify subdirectories that contain the actual test files
        # (e.g. test/unit/, test/integration/) and avoid __init__.py-only roots.
        subdirs: set[str] = set()
        for f in py_files:
            parts = f.relative_to(project_dir).parts
            if len(parts) >= 3:
                subdirs.add(f"{parts[0]}/{parts[1]}")
            else:
                subdirs.add(rel_root)

        testpaths_str = " ".join(sorted(subdirs))
        ini = (
            f"[pytest]\n"
            f"testpaths = {testpaths_str}\n"
            f"python_files = *.py\n"
            f"python_classes = Test*\n"
            f"python_functions = test_*\n"
        )

    (project_dir / "pytest.ini").write_text(ini, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
