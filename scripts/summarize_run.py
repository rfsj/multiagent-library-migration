"""Extract comparison metrics from the newest run of a task and append a row.

Usage: python3 experiments/summarize_run.py <task_id> <prompt_label>
"""

from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def newest_run(task_id: str) -> Path:
    runs = sorted(glob.glob(str(ROOT / "experiments" / "runs" / f"{task_id}_*")))
    if not runs:
        raise SystemExit(f"no run dir for {task_id}")
    return Path(runs[-1])


def main() -> int:
    task_id, prompt = sys.argv[1], sys.argv[2]
    run = newest_run(task_id)
    report = json.loads((run / "report.json").read_text())

    # Did any post-processing layer touch the raw LLM output? (AST/rescan/etc.)
    layers = set()
    raw_equals_final = True
    plan_chars = 0
    for log in (run / "logs").glob("*_migration.json"):
        data = json.loads(log.read_text())
        pipes = []
        if "pipeline" in data:
            pipes.append(data["pipeline"])
        for fr in data.get("file_results", []):
            if "pipeline" in fr:
                pipes.append(fr["pipeline"])
        for p in pipes:
            layers.update(p.get("layers_active", []))
            if not p.get("raw_equals_final", True):
                raw_equals_final = False
            plan_chars = max(plan_chars, len(p.get("migration_plan", "") or ""))

    summ = report.get("migration_step_summary", {})
    row = {
        "task": task_id,
        "prompt": prompt,
        "status": report.get("status"),
        "tests_after": report.get("tests_after"),
        "accepted": f"{summ.get('accepted_steps', '?')}/{summ.get('planned_steps', '?')}",
        "total_retries": report.get("total_retries"),
        "unmigrated_uses": report.get("unmigrated_uses"),
        "out_of_scope": report.get("out_of_scope_changes"),
        "llm_calls": report.get("llm_calls"),
        "layers_active": sorted(layers),
        "raw_equals_final": raw_equals_final,
        "plan_chars": plan_chars,
        "run_dir": run.name,
    }
    out = ROOT / "experiments" / os.getenv("COMPARISON_OUT", "prompt_comparison.jsonl")
    with out.open("a") as f:
        f.write(json.dumps(row) + "\n")
    print(
        f"{prompt:>5} | {task_id:<32} | status={row['status']:<8} "
        f"tests={row['tests_after']:<7} accepted={row['accepted']:<5} "
        f"retries={row['total_retries']} plan={row['plan_chars']}c "
        f"layers={row['layers_active']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
