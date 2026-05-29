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


if __name__ == "__main__":
    raise SystemExit(main())
