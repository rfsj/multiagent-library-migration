from __future__ import annotations

import filecmp
import subprocess
from pathlib import Path
from typing import Any

IGNORED_PARTS = {".venv", "__pycache__", ".pytest_cache"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}


def changed_files(before_dir: Path, after_dir: Path) -> list[str]:
    changed: list[str] = []
    before_paths = {
        str(path.relative_to(before_dir))
        for path in before_dir.rglob("*")
        if path.is_file() and not _ignored(path)
    }
    after_paths = {
        str(path.relative_to(after_dir))
        for path in after_dir.rglob("*")
        if path.is_file() and not _ignored(path)
    }
    for rel in sorted(before_paths | after_paths):
        before = before_dir / rel
        after = after_dir / rel
        if (
            not before.exists()
            or not after.exists()
            or not filecmp.cmp(before, after, shallow=False)
        ):
            changed.append(rel)
    return changed


def unified_diff(before_dir: Path, after_dir: Path) -> str:
    proc = subprocess.run(
        [
            "diff",
            "-ruN",
            "-x",
            ".pytest_cache",
            "-x",
            "__pycache__",
            "-x",
            "*.pyc",
            str(before_dir),
            str(after_dir),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return proc.stdout


def analyze_diff(
    before_dir: Path,
    after_dir: Path,
    allowed_files: list[str] | None = None,
) -> dict[str, Any]:
    changed = changed_files(before_dir, after_dir)
    if allowed_files is None:
        expected_prefixes = ("src/", "requirements.txt")
        out_of_scope = [
            path
            for path in changed
            if not (path == "requirements.txt" or path.startswith(expected_prefixes))
        ]
    else:
        allowed = set(allowed_files)
        out_of_scope = [path for path in changed if path not in allowed]
    return {
        "changed_files": changed,
        "out_of_scope_changes": len(out_of_scope),
        "out_of_scope_files": out_of_scope,
    }


def _ignored(path: Path) -> bool:
    return (
        bool(IGNORED_PARTS.intersection(path.parts)) or path.suffix in IGNORED_SUFFIXES
    )
