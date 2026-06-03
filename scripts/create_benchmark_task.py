from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("project_path")
    parser.add_argument("--source-library", default="pandas")
    parser.add_argument("--target-library", default="polars")
    parser.add_argument("--description", default=None)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    source = Path(args.project_path).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        raise FileNotFoundError(f"Project path does not exist or is not a directory: {source}")

    task_dir = ROOT / "benchmark" / args.task_id
    input_project = task_dir / "input_project"
    if task_dir.exists() and not args.overwrite:
        raise FileExistsError(
            f"Benchmark task already exists: {task_dir}. Use --overwrite to replace it."
        )
    if task_dir.exists():
        shutil.rmtree(task_dir)

    ignore = shutil.ignore_patterns(
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        "node_modules",
    )
    shutil.copytree(source, input_project, ignore=ignore)

    metadata = {
        "task_id": args.task_id,
        "source_library": args.source_library,
        "target_library": args.target_library,
        "description": args.description
        or f"Real project benchmark for {args.source_library} to {args.target_library} migration.",
        "expected_changed_files": [],
    }
    (task_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "task_id": args.task_id,
        "task_dir": str(task_dir),
        "input_project": str(input_project),
        "metadata": str(task_dir / "metadata.json"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
