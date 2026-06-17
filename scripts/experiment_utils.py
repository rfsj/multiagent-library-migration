from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import llm_proxy


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_task_metadata(task_id: str) -> dict[str, Any]:
    task_dir = ROOT / "benchmark" / task_id
    return json.loads((task_dir / "metadata.json").read_text(encoding="utf-8"))


def create_eval_run_dir(task_id: str, phase: str, run_id: str | None = None) -> Path:
    suffix = run_id or utc_timestamp()
    run_dir = ROOT / "experiments" / "runs" / f"{task_id}_{suffix}_{phase}"
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    return run_dir


def setup_project_copy(task_id: str, run_dir: Path) -> tuple[Path, Path]:
    task_dir = ROOT / "benchmark" / task_id
    project_dir = run_dir / "project"
    before_dir = run_dir / "snapshots" / "before_migration"
    shutil.copytree(task_dir / "input_project", project_dir)
    shutil.copytree(project_dir, before_dir)
    copy_prompts(run_dir)
    return project_dir, before_dir


def copy_prompts(run_dir: Path) -> None:
    target = run_dir / "prompts"
    target.mkdir(parents=True, exist_ok=True)
    for prompt in (ROOT / "prompts").glob("*.md"):
        shutil.copy2(prompt, target / prompt.name)


def configure_llm_logging(logs_dir: Path) -> None:
    llm_proxy.configure(logs_dir / "llm_proxy.jsonl")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2))


def allowed_files_from_diagnosis(diagnosis: dict[str, Any]) -> list[str]:
    allowed = set()
    for step in diagnosis.get("migration_steps", []):
        allowed.update(step.get("allowed_files", []))
    return sorted(allowed)


def enrich_step(step: dict[str, Any], diagnosis: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(step)
    enriched["source_library"] = diagnosis.get("source_library")
    enriched["target_library"] = diagnosis.get("target_library")
    enriched["dataframe_flow_analysis"] = diagnosis.get("dataframe_flow_analysis", {})
    return enriched


def llm_call_summary() -> dict[str, Any]:
    return {
        "total": llm_proxy.total_calls(),
        "by_label": llm_proxy.call_counts(),
    }


def env_snapshot() -> dict[str, str | None]:
    keys = [
        "DIAGNOSIS_AGENT_IMPL",
        "DIAGNOSIS_USE_AST",
        "LLM_PROVIDER",
        "LLM_MODEL",
        "MIGRATION_MODE",
        "PLANNER_USE_SYMBOL_ANALYSIS",
        "MIGRATION_USE_AST",
        "MIGRATION_USE_SCOPE",
        "MIGRATION_USE_COT",
        "MIGRATION_USE_FEWSHOT",
    ]
    return {key: os.getenv(key) for key in keys}


def success_from_report(report: dict[str, Any]) -> bool:
    return report.get("status") == "success"


def pass_at_k(successes: list[bool], k_values: list[int]) -> dict[str, bool | None]:
    result: dict[str, bool | None] = {}
    for k in k_values:
        result[f"pass@{k}"] = any(successes[:k]) if len(successes) >= k else None
    return result


def pass_caret_k(successes: list[bool], k_values: list[int]) -> dict[str, bool | None]:
    result: dict[str, bool | None] = {}
    for k in k_values:
        result[f"pass^{k}"] = all(successes[:k]) if len(successes) >= k else None
    return result


def cost_to_success(reports: list[dict[str, Any]]) -> dict[str, Any]:
    total_llm_calls = 0
    for index, report in enumerate(reports, start=1):
        calls = report.get("llm_calls", {}).get("total", 0)
        total_llm_calls += calls
        if success_from_report(report):
            return {
                "first_success_rank": index,
                "llm_calls_to_first_success": total_llm_calls,
                "run_dir": report.get("run_dir"),
            }
    return {
        "first_success_rank": None,
        "llm_calls_to_first_success": total_llm_calls,
        "run_dir": None,
    }
