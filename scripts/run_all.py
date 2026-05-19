from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    task_ids = sorted(path.name for path in (ROOT / "benchmark").iterdir() if path.is_dir())
    failures = 0
    for task_id in task_ids:
        proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_task.py"), task_id], check=False)
        failures += int(proc.returncode != 0)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
