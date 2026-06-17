"""Render a human-friendly, self-contained HTML report from evaluation
artifacts produced by this project's eval scripts.

No external dependencies (stdlib only) and no network access (no CDN
fonts/JS) — the output HTML opens standalone in any browser.

Supported inputs (auto-detected):

1. A `run_evaluation_matrix.py` output directory
   (`experiments/evaluations/matrix_<timestamp>/`, contains
   `matrix_report.json`) -> ablation + pass@k + run-level tables across
   configs and tasks. This is the end-to-end, "did this actually work"
   report.

2. A `run_planner_matrix.py` output directory
   (`experiments/evaluations/planner_matrix_<timestamp>/`, contains
   `planner_matrix_report.json`) -> planner-only comparison: valid-plan rate,
   planner pass@k/pass^k, validity submetrics, step-count variance,
   human-review rate, warnings, and cost. Use this to validate the planner in
   isolation before blaming migration/validation.

3. A single `eval_full.py` output file (`*_full_eval.json`) -> one-task
   pass@k report.

4. A single `eval_planner_only.py` output file (`planner_only_report.json`)
   -> one planner run, rendered as a small fact sheet.

Usage:
    .venv/bin/python scripts/generate_html_report.py experiments/evaluations/matrix_<ts>/
    .venv/bin/python scripts/generate_html_report.py experiments/evaluations/planner_matrix_<ts>/
    .venv/bin/python scripts/generate_html_report.py experiments/evaluations/<task>_<ts>_full_eval.json
    .venv/bin/python scripts/generate_html_report.py experiments/runs/<task>_<ts>_planner/planner_only_report.json

    # optional: --output path/to/report.html (default: report.html next to
    # the input directory, or <input>.html next to a single json file)
"""

from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path
from typing import Any

GREEN_VALUES = {"true", "success", "approved", "passed"}
RED_VALUES = {"false", "failed", "rejected"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "input", help="Matrix directory, planner-matrix directory, or a single eval *.json report."
    )
    parser.add_argument(
        "--output", default=None, help="Output HTML path. Default: report.html next to the input."
    )
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        parser.error(f"{input_path} does not exist.")

    kind, payload = _load(input_path)
    title, body = _render(kind, payload, input_path)
    document = _wrap_html(title, body)

    output_path = Path(args.output).resolve() if args.output else _default_output(input_path)
    output_path.write_text(document, encoding="utf-8")
    print(f"Wrote {kind} report -> {output_path}")
    return 0


def _default_output(input_path: Path) -> Path:
    if input_path.is_dir():
        return input_path / "report.html"
    return input_path.with_suffix(".html")


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def _load(input_path: Path) -> tuple[str, dict[str, Any]]:
    if input_path.is_dir():
        matrix_report = input_path / "matrix_report.json"
        planner_report = input_path / "planner_matrix_report.json"
        if matrix_report.exists():
            return "matrix", _load_matrix(input_path, matrix_report)
        if planner_report.exists():
            return "planner_matrix", _load_planner_matrix(input_path, planner_report)
        raise SystemExit(
            f"{input_path} does not look like a run_evaluation_matrix.py or "
            "run_planner_matrix.py output directory (missing "
            "matrix_report.json / planner_matrix_report.json)."
        )

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    phase = payload.get("phase")
    if phase == "full_workflow":
        return "full_eval", payload
    if phase == "evaluation_matrix":
        return "matrix", _load_matrix(input_path.parent, input_path)
    if phase == "planner_matrix":
        return "planner_matrix", _load_planner_matrix(input_path.parent, input_path)
    if phase == "planner_only":
        return "planner_only", payload
    raise SystemExit(
        f"Unrecognized report shape (phase={phase!r}) in {input_path}. "
        "Expected output from eval_full.py, eval_planner_only.py, "
        "run_evaluation_matrix.py, or run_planner_matrix.py."
    )


def _load_matrix(dir_path: Path, report_path: Path) -> dict[str, Any]:
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["_run_level_rows"] = _read_csv(dir_path / "run_level.csv")
    payload["_pass_at_k_rows"] = _read_csv(dir_path / "pass_at_k.csv")
    payload["_ablation_rows"] = _read_csv(dir_path / "ablation.csv")
    return payload


def _load_planner_matrix(dir_path: Path, report_path: Path) -> dict[str, Any]:
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["_run_rows"] = _read_csv(dir_path / "planner_run_level.csv")
    payload["_pass_at_k_rows"] = _read_csv(dir_path / "planner_pass_at_k.csv")
    payload["_ablation_rows"] = _read_csv(dir_path / "planner_ablation.csv")
    if not payload["_run_rows"]:
        payload["_run_rows"] = _read_csv(dir_path / "planner_runs.csv")
    if not payload["_ablation_rows"]:
        payload["_ablation_rows"] = _read_csv(dir_path / "planner_summary.csv")
    return payload


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# --------------------------------------------------------------------------
# Rendering: dispatch
# --------------------------------------------------------------------------

def _render(kind: str, payload: dict[str, Any], input_path: Path) -> tuple[str, str]:
    if kind == "matrix":
        return _render_matrix(payload)
    if kind == "planner_matrix":
        return _render_planner_matrix(payload)
    if kind == "full_eval":
        return _render_full_eval(payload)
    if kind == "planner_only":
        return _render_planner_only(payload)
    raise SystemExit(f"No renderer for kind={kind!r}")


def _render_matrix(payload: dict[str, Any]) -> tuple[str, str]:
    title = "Evaluation Matrix Report"
    meta = _meta_table({
        "Configs": ", ".join(payload.get("configs", [])),
        "Tasks": ", ".join(payload.get("tasks", [])),
        "Attempts per run": payload.get("attempts_per_run"),
        "k values": payload.get("k"),
        "Duration": _seconds(payload.get("duration_seconds")),
        "Source": payload.get("matrix_dir"),
    })

    ablation_rows = payload.get("_ablation_rows", [])
    ablation_cols = [
        ("config", "Config", "plain"),
        ("tasks", "Tasks", "plain"),
        ("success_rate", "Success rate", "rate"),
        ("pass@3", "pass@3", "rate"),
        ("pass@5", "pass@5", "rate"),
        ("avg_llm_calls", "Avg LLM calls", "plain"),
        ("avg_retries", "Avg retries", "plain"),
        ("scope_violation_rate", "Scope violations", "rate_inverted"),
        ("unmigrated_usage_rate", "Unmigrated usage", "rate_inverted"),
    ]

    pass_at_k_rows = payload.get("_pass_at_k_rows", [])
    pass_at_k_cols = [
        ("task_id", "Task", "plain"),
        ("config", "Config", "plain"),
        ("attempts", "Attempts", "plain"),
        ("success_rate", "Success rate", "rate"),
        ("pass@1", "pass@1", "badge"),
        ("pass@3", "pass@3", "badge"),
        ("pass@5", "pass@5", "badge"),
        ("pass^1", "pass^1", "badge"),
        ("pass^3", "pass^3", "badge"),
        ("pass^5", "pass^5", "badge"),
        ("first_success_rank", "First success @", "plain"),
        ("llm_calls_to_success", "LLM calls to success", "plain"),
    ]

    run_level_rows = payload.get("_run_level_rows", [])
    run_level_cols = [
        ("task_id", "Task", "plain"),
        ("config", "Config", "plain"),
        ("attempt", "#", "plain"),
        ("success", "Success", "badge"),
        ("tests_after", "Tests", "badge"),
        ("final_validation", "Validation", "badge"),
        ("out_of_scope_changes", "Scope violations", "count"),
        ("unmigrated_uses", "Unmigrated uses", "count"),
        ("retries", "Retries", "plain"),
        ("replans", "Replans", "plain"),
        ("llm_calls", "LLM calls", "plain"),
        ("run_dir", "Run dir", "path"),
    ]

    body = meta
    body += _section("Ablation summary", _table(ablation_cols, ablation_rows))
    body += _section("Pass@K / pass^k by task x config", _table(pass_at_k_cols, pass_at_k_rows))
    body += _section(
        "Run-level detail",
        _table(run_level_cols, run_level_rows, row_status_key="success"),
    )
    return title, body


def _render_planner_matrix(payload: dict[str, Any]) -> tuple[str, str]:
    title = "Planner-Only Matrix Report"
    meta = _meta_table({
        "Configs": ", ".join(payload.get("configs", [])),
        "Tasks": ", ".join(payload.get("tasks", [])),
        "Attempts per run": payload.get("attempts_per_run"),
        "k values": payload.get("k"),
        "Duration": _seconds(payload.get("duration_seconds")),
        "Source": payload.get("matrix_dir"),
    })

    ablation_rows = payload.get("_ablation_rows", [])
    ablation_cols = [
        ("config", "Config", "plain"),
        ("tasks", "Tasks", "plain"),
        ("attempts", "Attempts", "plain"),
        ("valid_plan_rate", "Valid plan rate", "rate"),
        ("planner_pass@3", "Planner pass@3", "rate"),
        ("planner_pass@5", "Planner pass@5", "rate"),
        ("avg_plan_validity_score", "Avg validity score", "rate"),
        ("file_coverage_rate", "File coverage", "rate"),
        ("symbol_coverage_rate", "Symbol coverage", "rate"),
        ("expected_step_coverage_rate", "Expected steps", "rate"),
        ("scope_violation_rate", "Scope violations", "rate_inverted"),
        ("dependency_plan_valid_rate", "Dependency valid", "rate"),
        ("step_order_valid_rate", "Step order valid", "rate"),
        ("human_review_match_rate", "Human-review match", "rate"),
        ("granularity_valid_rate", "Granularity valid", "rate"),
        ("human_review_rate", "Human-review rate", "rate"),
        ("step_count_min", "Steps (min)", "plain"),
        ("step_count_max", "Steps (max)", "plain"),
        ("step_count_mean", "Steps (mean)", "plain"),
        ("avg_llm_calls", "Avg LLM calls", "plain"),
        ("avg_duration_seconds", "Avg duration (s)", "plain"),
    ]

    pass_at_k_rows = payload.get("_pass_at_k_rows", [])
    pass_at_k_cols = [
        ("task_id", "Task", "plain"),
        ("config", "Config", "plain"),
        ("attempts", "Attempts", "plain"),
        ("valid_plan_rate", "Valid plan rate", "rate"),
        ("planner_pass@1", "Planner pass@1", "badge"),
        ("planner_pass@3", "Planner pass@3", "badge"),
        ("planner_pass@5", "Planner pass@5", "badge"),
        ("planner_pass^1", "Planner pass^1", "badge"),
        ("planner_pass^3", "Planner pass^3", "badge"),
        ("planner_pass^5", "Planner pass^5", "badge"),
        ("first_success_rank", "First success @", "plain"),
        ("llm_calls_to_success", "LLM calls to success", "plain"),
    ]

    run_rows = payload.get("_run_rows", [])
    run_cols = [
        ("task_id", "Task", "plain"),
        ("config", "Config", "plain"),
        ("attempt", "#", "plain"),
        ("valid_plan", "Valid plan", "badge"),
        ("status", "Status", "badge"),
        ("plan_validity_score", "Validity score", "rate"),
        ("file_coverage_rate", "File coverage", "rate"),
        ("scope_precision_rate", "Scope precision", "rate"),
        ("symbol_coverage_rate", "Symbol coverage", "rate"),
        ("expected_step_coverage_rate", "Expected steps", "rate"),
        ("dependency_plan_valid", "Dependency valid", "badge"),
        ("step_order_valid", "Step order", "badge"),
        ("human_review_match", "Review match", "badge"),
        ("granularity_valid", "Granularity", "badge"),
        ("missing_affected_source_files", "Missing affected files", "plain"),
        ("missing_allowed_source_files", "Missing allowed files", "plain"),
        ("unexpected_allowed_files", "Unexpected allowed files", "plain"),
        ("missing_required_symbols", "Missing symbols", "plain"),
        ("plan_violations", "Violations", "plain"),
        ("migration_step_count", "Steps", "plain"),
        ("human_review_required", "Human review", "badge"),
        ("affected_source_files", "Affected files", "plain"),
        ("llm_calls", "LLM calls", "plain"),
        ("duration_seconds", "Duration (s)", "plain"),
        ("planner_warnings", "Warnings", "plain"),
        ("run_dir", "Run dir", "path"),
    ]

    body = meta
    note = (
        "<p class='note'>Step-count min/max spread within a config is "
        "expected: plan <em>structure</em> (how many steps, which symbols "
        "share a step) is decided by the planner LLM call and is not fully "
        "deterministic even with symbol analysis on. A valid plan means the "
        "diagnosis satisfies the benchmark's expected_planner contract when "
        "present, or the project audit contract otherwise: covered target "
        "files/symbols, in-scope allowed files, required dependency step, and "
        "basic ordering constraints.</p>"
    )
    body += note
    body += _section("Plan validity summary", _table(ablation_cols, ablation_rows))
    body += _section("Planner pass@K / pass^k by task x config", _table(pass_at_k_cols, pass_at_k_rows))
    body += _section(
        "Plan validation detail",
        _table(run_cols, run_rows, row_status_key="valid_plan"),
    )
    return title, body


def _render_full_eval(payload: dict[str, Any]) -> tuple[str, str]:
    title = f"Full Evaluation Report — {payload.get('task_id', '')}"
    pass_at_k = payload.get("pass_at_k", {})
    pass_caret_k = payload.get("pass_caret_k", {})
    cost = payload.get("cost_to_success", {})

    meta = _meta_table({
        "Task": payload.get("task_id"),
        "Attempts completed": payload.get("attempts_completed"),
        "Success rate": _fmt_rate(payload.get("success_rate")),
        "First success @ attempt": cost.get("first_success_rank"),
        "LLM calls to first success": cost.get("llm_calls_to_first_success"),
        "Duration": _seconds(payload.get("duration_seconds")),
    })

    k_cols = [("metric", "Metric", "plain"), ("value", "Value", "badge")]
    k_rows = []
    for key, value in {**pass_at_k, **pass_caret_k}.items():
        k_rows.append({"metric": key, "value": value})

    attempt_cols = [
        ("attempt", "#", "plain"),
        ("success", "Success", "badge"),
        ("status", "Status", "badge"),
        ("tests_before", "Tests before", "badge"),
        ("tests_after", "Tests after", "badge"),
        ("final_validation_status", "Validation", "badge"),
        ("out_of_scope_changes", "Scope violations", "count"),
        ("unmigrated_uses", "Unmigrated uses", "count"),
        ("total_retries", "Retries", "plain"),
        ("replan_count", "Replans", "plain"),
        ("run_dir", "Run dir", "path"),
    ]
    attempt_rows = []
    for attempt in payload.get("attempts", []):
        row = dict(attempt)
        llm_calls = attempt.get("llm_calls") or {}
        row["llm_calls_total"] = llm_calls.get("total")
        attempt_rows.append(row)
    attempt_cols.insert(-1, ("llm_calls_total", "LLM calls", "plain"))

    body = meta
    body += _section("pass@K / pass^k", _table(k_cols, k_rows))
    body += _section(
        "Attempts", _table(attempt_cols, attempt_rows, row_status_key="success")
    )
    return title, body


def _render_planner_only(payload: dict[str, Any]) -> tuple[str, str]:
    title = f"Planner-Only Report — {payload.get('task_id', '')}"
    meta = _meta_table({
        "Task": payload.get("task_id"),
        "Planner version": payload.get("planner_version"),
        "Valid plan": payload.get("valid_plan"),
        "Plan validity score": _fmt_rate(payload.get("plan_validity_score")),
        "Migration step count": payload.get("migration_step_count"),
        "Human review required": payload.get("human_review_required"),
        "Affected source files": ", ".join(payload.get("affected_source_files", [])),
        "LLM calls": (payload.get("llm_calls") or {}).get("total"),
        "Duration": _seconds(payload.get("duration_seconds")),
        "Run dir": payload.get("run_dir"),
    })

    warnings = payload.get("planner_warnings", [])
    reasons = payload.get("human_review_reasons", [])
    violations = payload.get("plan_violations", [])
    body = meta
    if violations:
        body += _section("Plan violations", _list(_format_violation_list(violations)))
    if warnings:
        body += _section("Planner warnings", _list(warnings))
    if reasons:
        body += _section("Human-review reasons", _list(reasons))
    return title, body


# --------------------------------------------------------------------------
# Rendering: small building blocks
# --------------------------------------------------------------------------

def _section(heading: str, inner_html: str) -> str:
    return f"<section><h2>{html.escape(heading)}</h2>{inner_html}</section>"


def _meta_table(fields: dict[str, Any]) -> str:
    rows = "".join(
        f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(_text(value))}</td></tr>"
        for key, value in fields.items()
        if value not in (None, "")
    )
    return f"<table class='meta'>{rows}</table>"


def _list(items: list[str]) -> str:
    li = "".join(f"<li>{html.escape(str(item))}</li>" for item in items)
    return f"<ul>{li}</ul>"


def _format_violation_list(violations: list[dict[str, Any]]) -> list[str]:
    items = []
    for violation in violations:
        code = violation.get("code", "violation")
        severity = violation.get("severity", "")
        location = violation.get("path") or violation.get("file") or violation.get("step_id")
        message = violation.get("message", "")
        prefix = f"{severity}:{code}" if severity else str(code)
        if location:
            prefix = f"{prefix} ({location})"
        items.append(f"{prefix} - {message}" if message else prefix)
    return items


def _table(
    columns: list[tuple[str, str, str]],
    rows: list[dict[str, Any]],
    row_status_key: str | None = None,
) -> str:
    if not rows:
        return "<p class='empty'>No data.</p>"

    head = "".join(f"<th>{html.escape(label)}</th>" for _, label, _ in columns)
    body_rows = []
    for row in rows:
        row_class = ""
        if row_status_key is not None:
            row_class = _row_class(row.get(row_status_key))
        cells = "".join(_cell(row.get(key), kind) for key, _, kind in columns)
        body_rows.append(f"<tr class='{row_class}'>{cells}</tr>")

    return (
        f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table></div>"
    )


def _row_class(value: Any) -> str:
    normalized = _text(value).strip().lower()
    if normalized in GREEN_VALUES:
        return "row-good"
    if normalized in RED_VALUES:
        return "row-bad"
    return ""


def _cell(value: Any, kind: str) -> str:
    if kind == "badge":
        return f"<td>{_badge(value)}</td>"
    if kind == "rate":
        return f"<td>{_rate_bar(value)}</td>"
    if kind == "rate_inverted":
        return f"<td>{_rate_bar(value, invert=True)}</td>"
    if kind == "count":
        return f"<td>{_count_badge(value)}</td>"
    if kind == "path":
        return f"<td class='path'>{html.escape(_text(value))}</td>"
    return f"<td>{html.escape(_text(value))}</td>"


def _badge(value: Any) -> str:
    text = _text(value)
    normalized = text.strip().lower()
    if normalized in GREEN_VALUES:
        css = "badge badge-green"
    elif normalized in RED_VALUES:
        css = "badge badge-red"
    elif normalized in ("", "none", "-"):
        css = "badge badge-gray"
        text = "–"
    else:
        css = "badge badge-gray"
    return f"<span class='{css}'>{html.escape(text)}</span>"


def _count_badge(value: Any) -> str:
    number = _as_float(value)
    text = _text(value) or "0"
    if number is None:
        return f"<span class='badge badge-gray'>{html.escape(text)}</span>"
    css = "badge badge-red" if number > 0 else "badge badge-green"
    return f"<span class='{css}'>{html.escape(text)}</span>"


def _rate_bar(value: Any, invert: bool = False) -> str:
    number = _as_float(value)
    if number is None:
        return "<span class='muted'>–</span>"
    pct = max(0.0, min(1.0, number)) * 100
    good = pct < 33.4 if invert else pct >= 66.7
    bad = pct >= 66.7 if invert else pct < 33.4
    color = "#1f9d55" if good else ("#d64545" if bad else "#d9a017")
    return (
        "<div class='bar-wrap'>"
        f"<div class='bar' style='width:{pct:.0f}%;background:{color}'></div>"
        f"<span class='bar-label'>{pct:.0f}%</span>"
        "</div>"
    )


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() == "none":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _fmt_rate(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return ""
    return f"{number * 100:.0f}%"


def _seconds(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return ""
    if number < 90:
        return f"{number:.1f}s"
    return f"{number / 60:.1f}min"


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


# --------------------------------------------------------------------------
# Page shell
# --------------------------------------------------------------------------

def _wrap_html(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<main>
<h1>{html.escape(title)}</h1>
{body}
<footer>Generated by scripts/generate_html_report.py</footer>
</main>
</body>
</html>
"""


_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  background: #f5f6f8;
  color: #1c1f26;
  margin: 0;
  padding: 2rem 1rem 4rem;
}
main { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 1.6rem; margin-bottom: 1.2rem; }
h2 { font-size: 1.1rem; margin: 0 0 0.6rem; color: #2a2f3a; }
section { margin: 1.6rem 0; background: #fff; border: 1px solid #e3e5ea; border-radius: 10px; padding: 1.1rem 1.2rem; box-shadow: 0 1px 2px rgba(0,0,0,0.03); }
table.meta { border-collapse: collapse; margin-bottom: 1.4rem; background: #fff; border: 1px solid #e3e5ea; border-radius: 10px; overflow: hidden; }
table.meta th, table.meta td { padding: 0.45rem 0.9rem; text-align: left; font-size: 0.9rem; border-bottom: 1px solid #eef0f3; }
table.meta th { color: #6b7280; font-weight: 600; white-space: nowrap; }
table.meta tr:last-child th, table.meta tr:last-child td { border-bottom: none; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 0.85rem; }
thead th { position: sticky; top: 0; background: #2a2f3a; color: #fff; text-align: left; padding: 0.5rem 0.7rem; font-weight: 600; white-space: nowrap; }
tbody td { padding: 0.45rem 0.7rem; border-bottom: 1px solid #eef0f3; white-space: nowrap; }
tbody tr:hover { background: #fafbfc; }
tr.row-good { background: #f1faf4; }
tr.row-bad { background: #fdf2f2; }
td.path { white-space: normal; word-break: break-all; color: #6b7280; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.78rem; max-width: 360px; }
.badge { display: inline-block; padding: 0.15rem 0.55rem; border-radius: 999px; font-size: 0.78rem; font-weight: 600; }
.badge-green { background: #e3f6e9; color: #1f9d55; }
.badge-red { background: #fbe7e7; color: #c0392b; }
.badge-gray { background: #eceef1; color: #5b6270; }
.bar-wrap { position: relative; width: 96px; height: 14px; background: #eceef1; border-radius: 7px; overflow: hidden; }
.bar { position: absolute; left: 0; top: 0; height: 100%; border-radius: 7px; }
.bar-label { position: absolute; right: 6px; top: -2px; font-size: 0.68rem; color: #3a3f4a; }
.muted { color: #9aa0ab; }
.empty { color: #9aa0ab; font-style: italic; }
.note { color: #5b6270; font-size: 0.85rem; background: #fff8e6; border: 1px solid #f3e2ad; border-radius: 8px; padding: 0.6rem 0.9rem; }
footer { margin-top: 2.5rem; color: #9aa0ab; font-size: 0.78rem; text-align: center; }
"""


if __name__ == "__main__":
    raise SystemExit(main())
