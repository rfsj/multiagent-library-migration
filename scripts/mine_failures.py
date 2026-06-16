#!/usr/bin/env python3
"""Data-driven discovery of where the LLM fails at migration.

Instead of hand-curating a catalog of "patterns the LLM gets wrong" (the
pattern_scanner's guidance, the few-shot examples), this tool *measures* it from the
runs already on disk, using the catalog-free generic detector (``src/tools/source_api``).
It reads the RAW first-pass LLM output (``pipeline.raw_llm_code``, captured before
scanner/rescan/AST touch it) and the original sources, and reports two views per model:

  - [resíduo]  source-library constructs the model left UNCONVERTED in its raw output;
  - [conversão-errada]  constructs that were in the ORIGINAL but no longer remain as
    residue (so they were "converted"), yet the step still failed — cross-referenced
    with the pytest/verdict evidence to attribute the failure.

Detection is library-agnostic (derived by introspection), so this works for any
source→target pair, not just pandas→polars. Output: a printed summary and
``experiments/failure_profile.json``.

Usage:
    python3 scripts/mine_failures.py
    python3 scripts/mine_failures.py --task task_003_multi_file_pandas_ops
    python3 scripts/mine_failures.py --runs-dir experiments/runs --out experiments/failure_profile.json

See ai_docs/proposal-failure-mining.md for the research rationale.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tools.source_api import detect_source_api

# Ubiquitous constructors / IO that convert 1:1 and only add co-occurrence noise.
_TRIVIAL = {
    "DataFrame",
    "Series",
    "read_csv",
    "read_json",
    "read_parquet",
    "read_excel",
}


def source_constructs(code: str, source_library: str, target_library: str) -> set[str]:
    return {
        hit.name
        for hit in detect_source_api(code, source_library, target_library)
        if hit.name not in _TRIVIAL
    }


# Error classes looked up in pytest output / verdict rationale, polars-specific first,
# then generic, then AssertionError (a semantic mismatch: code ran but output differed —
# the signature of a *wrong* conversion).
_ERROR_CLASSES = (
    "ColumnNotFoundError",
    "SchemaError",
    "ComputeError",
    "InvalidOperationError",
    "DuplicateError",
    "ImportError",
    "ModuleNotFoundError",
    "TypeError",
    "AttributeError",
    "ValueError",
    "KeyError",
    "AssertionError",
)


def classify_error(text: str) -> str:
    for cls in _ERROR_CLASSES:
        if cls in text:
            return cls
    return "other"


# Lexical names (pandas + polars) by which a construct shows up in failure text.
# This is NOT guidance (it never tells the model how to fix anything) — it is a
# measurement aid to attribute a failure to the construct the evidence implicates,
# instead of blaming every construct that merely co-occurs in the file.
_CONSTRUCT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "pivot_table": ("pivot",),
    "sort_values": ("sort", "ascending", "descending"),
    "merge": ("merge", "join"),
    "merge_asof": ("merge_asof", "asof", "join_asof"),
    "groupby": ("groupby", "group_by"),
    "resample": ("resample", "group_by_dynamic"),
    "to_datetime": ("to_datetime", "to_date", "datetime"),
    "fillna": ("fillna", "fill_null"),
    "reset_index": ("reset_index",),
    "drop_duplicates": ("drop_duplicates", "duplicate", "unique"),
    "pct_change": ("pct_change",),
    "apply": ("apply", "map_elements", "lambda"),
    "astype": ("astype", "cast", "dtype"),
    "boolean_indexing": ("filter", "boolean", "mask"),
    "column_assign": ("with_columns", "assignment"),
    "isin": ("isin", "is_in"),
    "isna": ("isna", "is_null"),
    "notna": ("notna", "is_not_null"),
    "nlargest": ("nlargest", "top_k"),
    "nsmallest": ("nsmallest", "bottom_k"),
    "iterrows": ("iterrows", "iter_rows"),
}


def _implicated(construct: str, evidence: str) -> bool:
    keywords = _CONSTRUCT_KEYWORDS.get(construct)
    if not keywords:
        keywords = tuple(tok for tok in construct.split("_") if len(tok) >= 4) or (
            construct,
        )
    low = evidence.lower()
    return any(kw in low for kw in keywords)


@dataclass
class PatternStat:
    raw_left: int = 0  # files where the LLM left this construct in raw output
    runs: set[str] = field(default_factory=set)
    in_failed_run: int = 0  # of those, how many in a run that ended failed
    fixed_by_ast: int = 0  # of those, how many where the AST layer changed the file


@dataclass
class WrongConvStat:
    """A construct present in the ORIGINAL, no longer residue in the raw output (so the
    LLM 'converted' it), yet the step still failed — a wrong-conversion suspect."""

    failed_steps: int = 0  # broad: converted and co-occurred in a failed step
    implicated: int = 0  # sharp: the failure text names this construct
    runs: set[str] = field(default_factory=set)
    error_classes: Counter = field(default_factory=Counter)
    example_rationale: str = ""


@dataclass
class ModelProfile:
    runs_analyzed: set[str] = field(default_factory=set)
    files_analyzed: int = 0
    files_with_raw_residue: int = 0
    residue: dict[str, PatternStat] = field(
        default_factory=lambda: defaultdict(PatternStat)
    )
    wrong_conversion: dict[str, WrongConvStat] = field(
        default_factory=lambda: defaultdict(WrongConvStat)
    )


def iter_runs(
    runs_dir: Path, task_filter: str | None
) -> Iterator[tuple[Path, dict[str, Any]]]:
    for run_dir in sorted(runs_dir.iterdir()):
        report = _load_json(run_dir / "report.json")
        if report is None:
            continue
        if task_filter and report.get("task_id") != task_filter:
            continue
        yield run_dir, report


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def iter_migrated_files(run_dir: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield (file, pipeline) for every migrated file in a run (single + grouped steps)."""
    logs_dir = run_dir / "logs"
    if not logs_dir.is_dir():
        return
    for log_path in sorted(logs_dir.glob("*_migration.json")):
        log = _load_json(log_path)
        if log is None:
            continue
        if "file_results" in log:
            for fr in log["file_results"]:
                if fr.get("pipeline"):
                    yield fr.get("file", log.get("file", "?")), fr["pipeline"]
        elif log.get("pipeline"):
            yield log.get("file", "?"), log["pipeline"]


def _step_pipelines(migration_log: dict[str, Any]) -> list[dict[str, Any]]:
    if "file_results" in migration_log:
        return [
            fr["pipeline"] for fr in migration_log["file_results"] if fr.get("pipeline")
        ]
    if migration_log.get("pipeline"):
        return [migration_log["pipeline"]]
    return []


def step_has_raw(migration_log: dict[str, Any]) -> bool:
    """True only when this step has captured raw LLM output — without it we cannot
    tell 'converted but wrong' from 'left as residue', so the step is skipped."""
    return any(p.get("raw_llm_code") for p in _step_pipelines(migration_log))


def step_file_list(migration_log: dict[str, Any]) -> list[str]:
    files = migration_log.get("files")
    if files:
        return list(files)
    single = migration_log.get("file")
    return [single] if single else []


def step_raw_residue(
    migration_log: dict[str, Any], source_library: str, target_library: str
) -> set[str]:
    residue: set[str] = set()
    for pipeline in _step_pipelines(migration_log):
        raw = pipeline.get("raw_llm_code")
        if raw:
            residue.update(source_constructs(raw, source_library, target_library))
    return residue


def mine_wrong_conversions(
    run_dir: Path,
    report: dict[str, Any],
    source_library: str,
    target_library: str,
    profile: ModelProfile,
) -> None:
    """For each step the run gave up on, attribute the failure to constructs that were
    in the ORIGINAL but 'converted' (no longer residue) — converted, yet still failed."""
    before_dir = run_dir / "snapshots" / "before_migration"
    logs_dir = run_dir / "logs"
    run_id = run_dir.name
    for failed in report.get("failed_steps", []):
        step_id = failed.get("step_id")
        if not step_id:
            continue
        migration_log = _load_json(logs_dir / f"{step_id}_migration.json") or {}
        if not step_has_raw(migration_log):
            continue  # no raw capture → cannot separate converted from residue
        files = step_file_list(migration_log) or (
            [failed["file"]] if failed.get("file") else []
        )

        original_constructs: set[str] = set()
        for rel in files:
            orig_path = before_dir / rel
            if orig_path.suffix == ".py" and orig_path.is_file():
                original_constructs.update(
                    source_constructs(
                        orig_path.read_text(encoding="utf-8"),
                        source_library,
                        target_library,
                    )
                )

        converted_but_failed = original_constructs - step_raw_residue(
            migration_log, source_library, target_library
        )
        if not converted_but_failed:
            continue

        validation = _load_json(logs_dir / f"{step_id}_validation.json") or {}
        evidence = (
            (validation.get("pytest_feedback", "") or "")
            + " "
            + (failed.get("rationale", "") or "")
        )
        error_class = classify_error(evidence)

        for construct in converted_but_failed:
            stat = profile.wrong_conversion[construct]
            stat.failed_steps += 1
            stat.runs.add(run_id)
            stat.error_classes[error_class] += 1
            if _implicated(construct, evidence):
                stat.implicated += 1
            if not stat.example_rationale and failed.get("rationale"):
                stat.example_rationale = failed["rationale"]


def mine(runs_dir: Path, task_filter: str | None) -> dict[str, ModelProfile]:
    profiles: dict[str, ModelProfile] = defaultdict(ModelProfile)
    for run_dir, report in iter_runs(runs_dir, task_filter):
        model = report.get("environment", {}).get("llm_model", "unknown")
        source_library = report.get("source_library", "pandas")
        target_library = report.get("target_library", "polars")
        run_failed = report.get("status") == "failed"
        run_id = run_dir.name
        profile = profiles[model]

        for _file, pipeline in iter_migrated_files(run_dir):
            raw = pipeline.get("raw_llm_code")
            if not raw:
                continue
            profile.runs_analyzed.add(run_id)
            profile.files_analyzed += 1

            constructs = source_constructs(raw, source_library, target_library)
            if constructs:
                profile.files_with_raw_residue += 1
            for name in constructs:
                stat = profile.residue[name]
                stat.raw_left += 1
                stat.runs.add(run_id)
                if run_failed:
                    stat.in_failed_run += 1
                if pipeline.get("changed_by_ast"):
                    stat.fixed_by_ast += 1

        mine_wrong_conversions(run_dir, report, source_library, target_library, profile)
    return profiles


def to_json(profiles: dict[str, ModelProfile]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for model, p in profiles.items():
        residue = sorted(
            (
                {
                    "construct": name,
                    "raw_left": s.raw_left,
                    "runs": len(s.runs),
                    "in_failed_run": s.in_failed_run,
                    "maybe_fixed_by_ast": s.fixed_by_ast,
                }
                for name, s in p.residue.items()
            ),
            key=lambda d: d["raw_left"],
            reverse=True,
        )
        wrong = sorted(
            (
                {
                    "construct": c,
                    "implicated": s.implicated,
                    "co_occurred_in_failed": s.failed_steps,
                    "runs": len(s.runs),
                    "error_classes": dict(s.error_classes.most_common()),
                    "example_rationale": s.example_rationale,
                }
                for c, s in p.wrong_conversion.items()
            ),
            key=lambda d: (d["implicated"], d["co_occurred_in_failed"]),
            reverse=True,
        )
        out[model] = {
            "runs_analyzed": len(p.runs_analyzed),
            "files_analyzed": p.files_analyzed,
            "files_with_raw_residue": p.files_with_raw_residue,
            "residue": residue,
            "wrong_conversion_suspects": wrong,
        }
    return out


def print_summary(profiles: dict[str, ModelProfile]) -> None:
    if not profiles:
        print(
            "Nenhum run com raw_llm_code encontrado. "
            "Rode tasks após a instrumentação de captura do passe cru."
        )
        return
    for model, p in profiles.items():
        print(f"\n=== {model} ===")
        print(
            f"runs: {len(p.runs_analyzed)} | arquivos analisados: {p.files_analyzed} "
            f"| com resíduo no passe cru: {p.files_with_raw_residue}"
        )

        print("  [resíduo] construções que o LLM deixou SEM converter no passe cru:")
        if not p.residue:
            print("    (nenhuma)")
        else:
            print(
                f"    {'construct':<24}{'raw_left':>9}{'runs':>6}{'failed':>8}{'ast?':>6}"
            )
            for name, s in sorted(
                p.residue.items(), key=lambda kv: kv[1].raw_left, reverse=True
            ):
                print(
                    f"    {name:<24}{s.raw_left:>9}{len(s.runs):>6}{s.in_failed_run:>8}{s.fixed_by_ast:>6}"
                )

        print("  [conversão-errada] convertida (sem resíduo) mas o step falhou —")
        print(
            "    'implic.' = a falha NOMEIA a construção; 'co-ocorr.' = só estava no arquivo:"
        )
        if not p.wrong_conversion:
            print("    (nenhuma)")
        else:
            print(
                f"    {'construct':<24}{'implic.':>8}{'co-ocorr.':>10}{'runs':>6}  erros"
            )
            ordered = sorted(
                p.wrong_conversion.items(),
                key=lambda kv: (kv[1].implicated, kv[1].failed_steps),
                reverse=True,
            )
            for c, s in ordered:
                errs = ", ".join(f"{e}×{n}" for e, n in s.error_classes.most_common(3))
                print(
                    f"    {c:<24}{s.implicated:>8}{s.failed_steps:>10}{len(s.runs):>6}  {errs}"
                )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", default=str(ROOT / "experiments" / "runs"))
    parser.add_argument("--task", default=None, help="Filtrar por task_id.")
    parser.add_argument(
        "--out", default=str(ROOT / "experiments" / "failure_profile.json")
    )
    args = parser.parse_args()

    runs_dir = Path(args.runs_dir)
    if not runs_dir.is_dir():
        print(f"runs-dir não encontrado: {runs_dir}")
        return 1

    profiles = mine(runs_dir, args.task)
    print_summary(profiles)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(to_json(profiles), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nPerfil escrito em {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
