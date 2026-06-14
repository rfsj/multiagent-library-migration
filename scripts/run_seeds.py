"""Run one task N times (seeds) and aggregate pass@k and quality metrics.

The migration mode/model are taken from the environment, exactly like a normal
run, so you compare conditions by setting MIGRATION_MODE:

    MIGRATION_MODE=research_cot python3 scripts/run_seeds.py task_021_groupby_transform --n 5

Each seed is a full `run_task.py` invocation (its own timestamped run dir). The
first seed installs project deps; the rest reuse them (override with
--skip-install to skip on every seed). Results are aggregated into
experiments/summary_<task>_<mode>_<timestamp>.json and printed.

pass@k uses the unbiased estimator from Chen et al. (2021): with n samples and c
correct, pass@k = 1 - C(n-c, k) / C(n, k).
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
from datetime import datetime, timezone
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def newest_run(task_id: str) -> Path:
    runs = sorted(glob.glob(str(ROOT / "experiments" / "runs" / f"{task_id}_*")))
    if not runs:
        raise SystemExit(f"no run dir for {task_id}")
    return Path(runs[-1])


def pass_at_k(n: int, c: int, k: int) -> float:
    """Probability that at least one of k draws is correct, given c/n correct."""
    if k > n:
        k = n
    if n - c < k:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)


def plan_chars(run: Path) -> int:
    """Largest migration_plan length across this run's migration steps."""
    best = 0
    for log in (run / "logs").glob("*_migration.json"):
        data = json.loads(log.read_text())
        pipes = []
        if "pipeline" in data:
            pipes.append(data["pipeline"])
        for fr in data.get("file_results", []):
            if "pipeline" in fr:
                pipes.append(fr["pipeline"])
        for p in pipes:
            best = max(best, len(p.get("migration_plan", "") or ""))
    return best


def seed_metrics(run: Path) -> dict:
    report = json.loads((run / "report.json").read_text())
    return {
        "run_dir": run.name,
        "mode": report.get("migration_config", {}).get("mode"),
        "status": report.get("status"),
        "tests_after": report.get("tests_after"),
        "total_retries": report.get("total_retries"),
        "plan_chars": plan_chars(run),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("--n", type=int, default=5, help="number of seeds (default 5)")
    parser.add_argument("--k", type=int, default=None, help="k for pass@k (default = n)")
    parser.add_argument(
        "--skip-install", action="store_true",
        help="skip dependency install on every seed (default: install only on seed 1)",
    )
    args = parser.parse_args()
    k = args.k or args.n

    seeds: list[dict] = []
    for i in range(1, args.n + 1):
        skip = args.skip_install or i > 1  # install once, then reuse
        cmd = [sys.executable, str(ROOT / "scripts" / "run_task.py"), args.task_id]
        if skip:
            cmd.append("--skip-install")
        print(f"[seed {i}/{args.n}] running {' '.join(cmd[2:])} ...", flush=True)
        subprocess.run(cmd, cwd=ROOT, check=False, stdout=subprocess.DEVNULL)
        m = seed_metrics(newest_run(args.task_id))
        seeds.append(m)
        print(
            f"           -> status={m['status']} tests={m['tests_after']} "
            f"retries={m['total_retries']} plan={m['plan_chars']}c"
        )

    n = len(seeds)
    succ = sum(1 for s in seeds if s["status"] == "success")
    tests_ok = sum(1 for s in seeds if s["tests_after"] == "passed")
    plan_ok = sum(1 for s in seeds if s["plan_chars"] > 0)
    retries = [s["total_retries"] or 0 for s in seeds]
    mode = seeds[0]["mode"]

    summary = {
        "task": args.task_id,
        "mode": mode,
        "n_seeds": n,
        "k": k,
        "successes": succ,
        "pass@1": round(succ / n, 3),                 # mean single-attempt success
        f"pass@{k}": round(pass_at_k(n, succ, k), 3),  # at least one of k
        "tests_pass_rate": round(tests_ok / n, 3),
        "plan_fill_rate": round(plan_ok / n, 3),
        "mean_retries": round(sum(retries) / n, 2),
        "seeds": seeds,
    }

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "experiments" / f"summary_{args.task_id}_{mode}_{ts}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n=== aggregate ===")
    print(
        f"{args.task_id} | mode={mode} | n={n} | "
        f"pass@1={summary['pass@1']} pass@{k}={summary[f'pass@{k}']} | "
        f"tests_pass={summary['tests_pass_rate']} | plan_fill={summary['plan_fill_rate']} | "
        f"mean_retries={summary['mean_retries']}"
    )
    print(f"saved: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
