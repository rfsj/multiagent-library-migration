#!/usr/bin/env python3
"""A/B spike: prompt problem vs model limitation, for the scanner guidance.

The mine_failures profile tells you *where* a model fails. This tells you *whether the
failure is fixable by the prompt*. It runs the same task N times with the scanner
guidance OFF vs ON — both in `research` mode, so the scanner's prompt hints are the
ONLY variable (no rescan, no AST) — and measures, per source construct, how often the
model leaves it UNCONVERTED in its raw output (residue).

Reading the result for a construct:
  - residue high with guidance OFF, drops with guidance ON  -> the model can do it once
    told how  -> **PROMPT PROBLEM** (improve the prompt / hint).
  - residue stays high even with guidance ON                -> the model ignores the
    hint  -> **MODEL LIMITATION** (needs a deterministic AST fix or a stronger model).

It also reports overall success rate (the prompt may fix non-conversion yet leave a
wrong conversion that only tests catch).

Usage:
    python3 scripts/ab_guidance.py task_001_read_csv_filter --runs 3
    python3 scripts/ab_guidance.py task_003_multi_file_pandas_ops --runs 3 --pattern boolean_indexing

LLMs are non-deterministic — use --runs >= 3 for a trend, more for confidence. Each run
is a real migration (real LLM calls). See ai_docs/proposal-failure-mining.md (step #4).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.mine_failures import iter_migrated_files, source_constructs

_RUNS_DIR = ROOT / "experiments" / "runs"


def run_once(task: str, scanner_on: bool) -> tuple[Path, dict] | None:
    """Run one migration in research mode with the scanner guidance on/off; return its
    (run_dir, report). Isolates the run by diffing the runs directory before/after."""
    before = {p.name for p in _RUNS_DIR.glob(f"{task}_*")} if _RUNS_DIR.exists() else set()
    env = {
        **os.environ,
        "MIGRATION_MODE": "research",
        "MIGRATION_USE_SCANNER": "1" if scanner_on else "0",
    }
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_task.py"), task],
        env=env,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    after = {p.name for p in _RUNS_DIR.glob(f"{task}_*")}
    # Newest new dir that actually produced a report — a run can crash mid-migration
    # (transient LLM failure) and leave a dir without report.json; skip those.
    for name in sorted(after - before, reverse=True):
        report_path = _RUNS_DIR / name / "report.json"
        if report_path.is_file():
            return _RUNS_DIR / name, json.loads(report_path.read_text(encoding="utf-8"))
    return None


def residue_constructs(run_dir: Path, source_library: str, target_library: str) -> set[str]:
    """Source constructs the model left unconverted in its raw first-pass output."""
    found: set[str] = set()
    for _file, pipeline in iter_migrated_files(run_dir):
        raw = pipeline.get("raw_llm_code")
        if raw:
            found |= source_constructs(raw, source_library, target_library)
    return found


def classify(off_rate: float, on_rate: float) -> str:
    if off_rate < 0.34:
        return "raro mesmo sem guidance (pouco sinal)"
    if on_rate <= off_rate / 2:
        return "PROMPT — a guidance reduz o resíduo"
    if on_rate >= 0.5:
        return "LIMITAÇÃO — persiste mesmo com guidance"
    return "inconclusivo (rode mais)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task")
    parser.add_argument("--runs", type=int, default=3, help="Runs por condição (>=3).")
    parser.add_argument("--pattern", default=None, help="Foco num construct específico.")
    args = parser.parse_args()

    # cond -> list of (status, residue_set); plus source/target libs captured from reports
    results: dict[str, list[tuple[str, set[str]]]] = {"off": [], "on": []}
    libs = {"source": "pandas", "target": "polars"}

    total = args.runs * 2
    done = 0
    for cond in ("off", "on"):
        for _ in range(args.runs):
            done += 1
            print(f"[{done}/{total}] {args.task} | guidance {cond.upper()} ...", flush=True)
            outcome = run_once(args.task, scanner_on=(cond == "on"))
            if outcome is None:
                print("  (run não produziu report — pulando)")
                continue
            run_dir, report = outcome
            libs["source"] = report.get("source_library", libs["source"])
            libs["target"] = report.get("target_library", libs["target"])
            residue = residue_constructs(run_dir, libs["source"], libs["target"])
            results[cond].append((report.get("status", "unknown"), residue))

    _report(results, args.pattern)
    return 0


def _rate(runs: list[tuple[str, set[str]]], predicate) -> float:
    return (sum(1 for r in runs if predicate(r)) / len(runs)) if runs else 0.0


def _report(results: dict[str, list[tuple[str, set[str]]]], focus: str | None) -> None:
    off, on = results["off"], results["on"]
    if not off or not on:
        print("\nRuns insuficientes em alguma condição.")
        return

    succ_off = _rate(off, lambda r: r[0] == "success")
    succ_on = _rate(on, lambda r: r[0] == "success")

    constructs = {c for _s, res in off + on for c in res}
    if focus:
        constructs = {focus}

    print("\n=== A/B guidance do scanner (research; única variável = hint no prompt) ===")
    print(f"runs por condição: off={len(off)} on={len(on)}")
    print(f"success rate:  OFF {succ_off:.0%}  →  ON {succ_on:.0%}")
    print("\nresíduo no passe cru (fração de runs em que o construct ficou SEM converter):")
    print(f"  {'construct':<22}{'OFF':>6}{'ON':>6}   veredito")
    rows = []
    for c in constructs:
        off_rate = _rate(off, lambda r, c=c: c in r[1])
        on_rate = _rate(on, lambda r, c=c: c in r[1])
        rows.append((c, off_rate, on_rate))
    for c, off_rate, on_rate in sorted(rows, key=lambda t: t[1], reverse=True):
        print(f"  {c:<22}{off_rate:>5.0%}{on_rate:>6.0%}   {classify(off_rate, on_rate)}")

    print("\nLembre: resíduo mede só NÃO-conversão. Se success sobe mas o resíduo já era "
          "baixo, a falha é conversão-errada (ver mine_failures), não prompt.")


if __name__ == "__main__":
    raise SystemExit(main())
