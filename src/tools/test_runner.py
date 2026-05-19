from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any


def run_pytest(project_dir: Path, log_file: Path) -> dict[str, Any]:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=project_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    log_file.write_text(proc.stdout, encoding="utf-8")
    return {
        "status": "passed" if proc.returncode == 0 else "failed",
        "passed": proc.returncode == 0,
        "returncode": proc.returncode,
        "log_file": str(log_file),
    }
