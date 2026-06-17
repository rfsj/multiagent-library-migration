from __future__ import annotations

from pathlib import Path
from typing import Any

from src.tools.diff_analyzer import analyze_diff, changed_files
from src.tools.project_scanner import build_project_audit, scan_project


def validate_migration_result(
    *,
    project_dir: Path,
    before_dir: Path,
    diagnosis: dict[str, Any],
    metadata: dict[str, Any],
    tests: dict[str, Any] | None = None,
    final_validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate a migration artifact independently of the agent that produced it.

    The default oracle is derived from the audited planner oracle because it
    already describes the files/symbols that legitimately belong to the
    migration. A task may override this with ``metadata["migration_oracle"]``.
    """
    source_library = metadata.get("source_library") or diagnosis.get("source_library")
    target_library = metadata.get("target_library") or diagnosis.get("target_library")
    oracle = _migration_oracle(metadata, diagnosis)

    changed = changed_files(before_dir, project_dir)
    diff = analyze_diff(
        before_dir,
        project_dir,
        allowed_files=oracle["allowed_changed_files"] or None,
    )
    source_scan = scan_project(project_dir, source_library)
    target_audit = build_project_audit(project_dir, target_library, source_library)

    required_changed = set(oracle["required_changed_files"])
    changed_set = set(changed)
    missing_required = sorted(required_changed - changed_set)
    forbidden_changed = sorted(set(oracle["forbidden_changed_files"]) & changed_set)

    target_usage_files = set(target_audit["affected_source_files"])
    required_target_usage_files = set(oracle["required_target_usage_files"])
    missing_target_usage = sorted(required_target_usage_files - target_usage_files)

    old_imports_remaining = len(source_scan["source_imports_in_source"])
    unmigrated_uses = len(source_scan["source_api_calls_in_source"])
    tests_passed = _tests_passed(tests)
    final_validation_approved = (
        final_validation.get("status") == "approved" if final_validation else None
    )

    violations = []
    if tests_passed is False:
        violations.append("tests_failed")
    if final_validation_approved is False:
        violations.append("final_validation_rejected")
    if diff["out_of_scope_changes"]:
        violations.append("out_of_scope_changes")
    if missing_required:
        violations.append("missing_required_changed_files")
    if forbidden_changed:
        violations.append("forbidden_changed_files")
    if old_imports_remaining:
        violations.append("old_imports_remaining")
    if unmigrated_uses:
        violations.append("unmigrated_uses")
    if missing_target_usage:
        violations.append("missing_target_usage")

    scope_compliance = diff["out_of_scope_changes"] == 0 and not forbidden_changed
    source_usage_removed = old_imports_remaining == 0 and unmigrated_uses == 0
    target_usage_added = not missing_target_usage
    behavior_preserved = tests_passed is True
    migration_success = (
        behavior_preserved
        and final_validation_approved is True
        and scope_compliance
        and not missing_required
        and source_usage_removed
        and target_usage_added
    )

    return {
        "migration_success": migration_success,
        "behavior_preserved": behavior_preserved,
        "tests_passed": tests_passed,
        "final_validation_approved": final_validation_approved,
        "source_usage_removed": source_usage_removed,
        "old_imports_remaining": old_imports_remaining,
        "unmigrated_uses": unmigrated_uses,
        "target_usage_added": target_usage_added,
        "target_usage_files": sorted(target_usage_files),
        "missing_target_usage_files": missing_target_usage,
        "scope_compliance": scope_compliance,
        "changed_files": changed,
        "required_changed_files": sorted(required_changed),
        "missing_required_changed_files": missing_required,
        "allowed_changed_files": oracle["allowed_changed_files"],
        "out_of_scope_changes": diff["out_of_scope_changes"],
        "out_of_scope_files": diff["out_of_scope_files"],
        "forbidden_changed_files": forbidden_changed,
        "diff_changed_files": diff["changed_files"],
        "diff_line_count": _diff_line_count(before_dir, project_dir),
        "violations": violations,
        "oracle_source": oracle["source"],
    }


def _migration_oracle(metadata: dict[str, Any], diagnosis: dict[str, Any]) -> dict[str, Any]:
    explicit = metadata.get("migration_oracle") or {}
    planner_oracle = metadata.get("validity_oracle") or metadata.get("expected_planner") or {}

    affected = _as_list(planner_oracle.get("affected_source_files"))
    allowed_source = _as_list(planner_oracle.get("allowed_source_files")) or affected
    allowed_dependency = _as_list(planner_oracle.get("allowed_dependency_files"))
    diagnosis_allowed = _diagnosis_allowed_files(diagnosis)

    allowed_changed = _as_list(explicit.get("allowed_changed_files"))
    if not allowed_changed:
        allowed_changed = sorted(set(allowed_source) | set(allowed_dependency) | set(diagnosis_allowed))

    required_changed = _as_list(explicit.get("required_changed_files"))
    if not required_changed:
        required_changed = affected

    required_target_usage = _as_list(explicit.get("required_target_usage_files"))
    if not required_target_usage:
        required_target_usage = affected

    return {
        "source": "migration_oracle" if explicit else "validity_oracle",
        "allowed_changed_files": sorted(set(allowed_changed)),
        "required_changed_files": sorted(set(required_changed)),
        "required_target_usage_files": sorted(set(required_target_usage)),
        "forbidden_changed_files": sorted(set(_as_list(explicit.get("forbidden_changed_files")))),
    }


def _diagnosis_allowed_files(diagnosis: dict[str, Any]) -> list[str]:
    allowed = set()
    for step in diagnosis.get("migration_steps", []) or []:
        allowed.update(step.get("allowed_files", []) or [])
    return sorted(allowed)


def _as_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []


def _tests_passed(tests: dict[str, Any] | None) -> bool | None:
    if not tests:
        return None
    if "passed" in tests:
        return bool(tests["passed"])
    status = str(tests.get("status", "")).lower()
    if status == "passed":
        return True
    if status == "failed":
        return False
    return None


def _diff_line_count(before_dir: Path, after_dir: Path) -> int:
    total = 0
    for rel in changed_files(before_dir, after_dir):
        before = before_dir / rel
        after = after_dir / rel
        before_lines = _read_lines(before) if before.exists() else []
        after_lines = _read_lines(after) if after.exists() else []
        total += abs(len(after_lines) - len(before_lines))
        total += sum(1 for old, new in zip(before_lines, after_lines) if old != new)
    return total


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return []
