from __future__ import annotations

from fnmatch import fnmatch
from typing import Any


def validate_plan(
    diagnosis: dict[str, Any],
    project_audit: dict[str, Any],
    validity_oracle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expected = validity_oracle or {}
    raw_steps = diagnosis.get("migration_steps", []) or []
    steps = raw_steps if isinstance(raw_steps, list) else []
    dependency_files = set(
        expected.get("allowed_dependency_files")
        or expected.get("dependency_files")
        or project_audit.get("dependency_files", [])
        or []
    )
    required_source_files = set(
        expected.get("required_source_files")
        or expected.get("affected_source_files")
        or project_audit.get("affected_source_files", [])
        or []
    )
    dependency_update_required = bool(
        expected.get(
            "dependency_update_required",
            project_audit.get("dependency_summary", {}).get("target_dependency_action")
            == "add_dependency",
        )
    )
    required_symbols = (
        expected.get("required_symbol_coverage")
        or expected.get("required_symbols")
        or {}
    )
    coverage_policy = expected.get("coverage_policy", "whole_file_or_all_symbols")
    allowed_granularity = set(expected.get("allowed_granularity", ["file", "symbol"]))
    forbidden_files = expected.get("forbidden_files", []) or []
    required_ordering = expected.get("required_ordering", []) or []
    expected_step_groups = expected.get("expected_step_groups", []) or []

    violations: list[dict[str, Any]] = []
    _validate_contract(diagnosis, raw_steps, violations)

    allowed_files_by_step = [_step_allowed_files(step) for step in steps]
    all_allowed_files = set().union(*allowed_files_by_step) if allowed_files_by_step else set()
    allowed_source_files = {
        path for path in all_allowed_files if path.endswith(".py") and path not in dependency_files
    }
    diagnosis_affected_source_files = set(diagnosis.get("affected_source_files", []) or [])

    missing_affected_source_files = sorted(required_source_files - diagnosis_affected_source_files)
    extra_affected_source_files = sorted(diagnosis_affected_source_files - required_source_files)
    missing_allowed_source_files = sorted(required_source_files - allowed_source_files)
    unexpected_allowed_files = sorted(
        path
        for path in all_allowed_files
        if path not in required_source_files and path not in dependency_files
    )

    _add_each(
        violations,
        "missing_affected_source_file",
        "error",
        missing_affected_source_files,
        "Required source file is missing from diagnosis affected_source_files.",
    )
    _add_each(
        violations,
        "extra_affected_source_file",
        "error",
        extra_affected_source_files,
        "Diagnosis affected_source_files contains a file outside the expected migration target set.",
    )
    _add_each(
        violations,
        "missing_allowed_source_file",
        "error",
        missing_allowed_source_files,
        "Required source file is not allowed by any migration step.",
    )
    _add_each(
        violations,
        "unexpected_allowed_file",
        "error",
        unexpected_allowed_files,
        "Migration step allows a file outside the expected source/dependency scope.",
    )
    _validate_forbidden_files(all_allowed_files, forbidden_files, violations)

    dependency_step_indexes = [
        index
        for index, allowed_files in enumerate(allowed_files_by_step)
        if allowed_files.intersection(dependency_files)
    ]
    source_step_indexes = [
        index
        for index, allowed_files in enumerate(allowed_files_by_step)
        if allowed_files.intersection(required_source_files)
    ]
    dependency_plan_valid = (
        not dependency_update_required
        or bool(dependency_step_indexes)
    )
    if not dependency_plan_valid:
        violations.append({
            "code": "missing_dependency_update_step",
            "severity": "error",
            "message": "Target dependency must be added but no dependency file is allowed by any step.",
        })

    step_order_valid = True
    if dependency_update_required and dependency_step_indexes and source_step_indexes:
        step_order_valid = min(dependency_step_indexes) <= min(source_step_indexes)
        if not step_order_valid:
            violations.append({
                "code": "dependency_step_after_source_step",
                "severity": "error",
                "message": "Dependency update should be planned before source migration steps.",
            })
    declared_order_valid = _validate_required_ordering(
        steps,
        dependency_files,
        required_source_files,
        required_ordering,
        violations,
    )
    step_order_valid = step_order_valid and declared_order_valid

    granularity_result = _validate_allowed_granularity(
        steps,
        required_source_files,
        allowed_granularity,
        violations,
    )
    symbol_result = _validate_required_symbols(
        steps,
        required_symbols,
        coverage_policy,
        violations,
    )
    expected_step_result = _validate_expected_step_groups(
        steps, expected_step_groups, violations
    )
    human_review_match = _validate_human_review(diagnosis, expected, violations)
    duplicate_scope_count = _validate_duplicate_scopes(steps, violations)

    migration_needed = bool(project_audit.get("migration_needed", required_source_files))
    if migration_needed and not steps:
        violations.append({
            "code": "missing_migration_steps",
            "severity": "error",
            "message": "Migration is needed but the planner produced no migration steps.",
        })

    file_coverage_rate = _coverage_rate(required_source_files, allowed_source_files)
    affected_file_coverage_rate = _coverage_rate(
        required_source_files, diagnosis_affected_source_files
    )
    scope_precision_rate = _precision_rate(
        all_allowed_files,
        required_source_files.union(dependency_files),
    )

    component_scores = [
        1.0 if not _has_contract_error(violations) else 0.0,
        file_coverage_rate,
        affected_file_coverage_rate,
        scope_precision_rate,
        1.0 if dependency_plan_valid else 0.0,
        1.0 if step_order_valid else 0.0,
        symbol_result["symbol_coverage_rate"],
        expected_step_result["expected_step_coverage_rate"],
        1.0 if human_review_match else 0.0,
        1.0 if duplicate_scope_count == 0 else 0.0,
        1.0 if granularity_result["granularity_valid"] else 0.0,
    ]
    plan_validity_score = round(sum(component_scores) / len(component_scores), 4)
    valid_plan = not any(
        violation.get("severity") == "error" for violation in violations
    )

    return {
        "valid_plan": valid_plan,
        "plan_validity_score": plan_validity_score,
        "file_coverage_rate": round(file_coverage_rate, 4),
        "affected_file_coverage_rate": round(affected_file_coverage_rate, 4),
        "scope_precision_rate": round(scope_precision_rate, 4),
        "symbol_coverage_rate": symbol_result["symbol_coverage_rate"],
        "expected_step_coverage_rate": expected_step_result["expected_step_coverage_rate"],
        "dependency_update_required": dependency_update_required,
        "dependency_plan_valid": dependency_plan_valid,
        "step_order_valid": step_order_valid,
        "human_review_match": human_review_match,
        "duplicate_step_scope_count": duplicate_scope_count,
        "granularity_valid": granularity_result["granularity_valid"],
        "expected_source_files": sorted(required_source_files),
        "planned_affected_source_files": sorted(diagnosis_affected_source_files),
        "planned_allowed_files": sorted(all_allowed_files),
        "missing_affected_source_files": missing_affected_source_files,
        "extra_affected_source_files": extra_affected_source_files,
        "missing_allowed_source_files": missing_allowed_source_files,
        "unexpected_allowed_files": unexpected_allowed_files,
        "missing_required_symbols": symbol_result["missing_required_symbols"],
        "missing_expected_step_groups": expected_step_result["missing_expected_step_groups"],
        "plan_violations": violations,
    }


def _validate_contract(
    diagnosis: dict[str, Any],
    steps: list[dict[str, Any]],
    violations: list[dict[str, Any]],
) -> None:
    required_root_fields = [
        "source_library",
        "target_library",
        "affected_source_files",
        "migration_steps",
    ]
    for field in required_root_fields:
        if field not in diagnosis:
            violations.append({
                "code": "missing_plan_field",
                "severity": "error",
                "field": field,
                "message": f"Diagnosis plan is missing required field {field}.",
            })
    if not isinstance(steps, list):
        violations.append({
            "code": "invalid_migration_steps_type",
            "severity": "error",
            "message": "migration_steps must be a list.",
        })
        return

    seen_step_ids: set[str] = set()
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            violations.append({
                "code": "invalid_step_type",
                "severity": "error",
                "step_index": index,
                "message": "Each migration step must be an object.",
            })
            continue
        step_id = step.get("step_id")
        if not step_id:
            violations.append({
                "code": "missing_step_id",
                "severity": "error",
                "step_index": index,
                "message": "Migration step is missing step_id.",
            })
        elif step_id in seen_step_ids:
            violations.append({
                "code": "duplicate_step_id",
                "severity": "error",
                "step_id": step_id,
                "message": "Migration step_id must be unique.",
            })
        seen_step_ids.add(step_id)

        if not step.get("allowed_files"):
            violations.append({
                "code": "missing_step_allowed_files",
                "severity": "error",
                "step_id": step_id,
                "message": "Migration step must declare allowed_files.",
            })
        for path in _step_declared_paths(step):
            if not _is_safe_relative_path(path):
                violations.append({
                    "code": "unsafe_plan_path",
                    "severity": "error",
                    "step_id": step_id,
                    "path": path,
                    "message": "Plan path must be a safe relative path.",
                })
            if _is_test_path(path):
                violations.append({
                    "code": "test_file_in_migration_scope",
                    "severity": "error",
                    "step_id": step_id,
                    "path": path,
                    "message": "Tests must not be migration targets.",
                })


def _validate_required_symbols(
    steps: list[dict[str, Any]],
    required_symbols: dict[str, list[str]],
    coverage_policy: str,
    violations: list[dict[str, Any]],
) -> dict[str, Any]:
    missing: dict[str, list[str]] = {}
    if not required_symbols:
        return {"symbol_coverage_rate": 1.0, "missing_required_symbols": missing}

    total = 0
    covered = 0
    for file, symbols in required_symbols.items():
        file_steps = [
            step for step in steps if file in _step_allowed_files(step)
        ]
        whole_file_planned = (
            coverage_policy == "whole_file_or_all_symbols"
            and any(not step.get("allowed_symbols") for step in file_steps)
        )
        planned_symbols = {
            symbol
            for step in file_steps
            for symbol in (step.get("allowed_symbols", []) or [])
        }
        for symbol in symbols:
            total += 1
            if whole_file_planned or symbol in planned_symbols:
                covered += 1
            else:
                missing.setdefault(file, []).append(symbol)

    for file, symbols in missing.items():
        violations.append({
            "code": "missing_required_symbol",
            "severity": "error",
            "file": file,
            "symbols": symbols,
            "message": "Required symbol is not covered by any matching migration step.",
        })

    return {
        "symbol_coverage_rate": round(covered / total, 4) if total else 1.0,
        "missing_required_symbols": missing,
    }


def _validate_expected_step_groups(
    steps: list[dict[str, Any]],
    expected_step_groups: list[dict[str, Any]],
    violations: list[dict[str, Any]],
) -> dict[str, Any]:
    missing: list[dict[str, Any]] = []
    if not expected_step_groups:
        return {
            "expected_step_coverage_rate": 1.0,
            "missing_expected_step_groups": missing,
        }

    for group in expected_step_groups:
        if not any(_step_matches_group(step, group) for step in steps):
            missing.append(group)
            violations.append({
                "code": "missing_expected_step_group",
                "severity": "error",
                "expected_step_group": group,
                "message": "No migration step satisfies this expected step group.",
            })

    covered = len(expected_step_groups) - len(missing)
    return {
        "expected_step_coverage_rate": round(covered / len(expected_step_groups), 4),
        "missing_expected_step_groups": missing,
    }


def _validate_allowed_granularity(
    steps: list[dict[str, Any]],
    required_source_files: set[str],
    allowed_granularity: set[str],
    violations: list[dict[str, Any]],
) -> dict[str, bool]:
    valid = True
    for step in steps:
        source_files = _step_allowed_files(step).intersection(required_source_files)
        if not source_files:
            continue
        step_granularity = "symbol" if step.get("allowed_symbols") else "file"
        if step_granularity not in allowed_granularity:
            valid = False
            violations.append({
                "code": "disallowed_step_granularity",
                "severity": "error",
                "step_id": step.get("step_id"),
                "granularity": step_granularity,
                "message": "Migration step granularity is not allowed by the validity oracle.",
            })
    return {"granularity_valid": valid}


def _validate_forbidden_files(
    allowed_files: set[str],
    forbidden_patterns: list[str],
    violations: list[dict[str, Any]],
) -> None:
    for path in sorted(allowed_files):
        for pattern in forbidden_patterns:
            if fnmatch(path, pattern):
                violations.append({
                    "code": "forbidden_file_allowed",
                    "severity": "error",
                    "path": path,
                    "pattern": pattern,
                    "message": "Migration step allows a file forbidden by the validity oracle.",
                })


def _validate_required_ordering(
    steps: list[dict[str, Any]],
    dependency_files: set[str],
    required_source_files: set[str],
    required_ordering: list[dict[str, str]],
    violations: list[dict[str, Any]],
) -> bool:
    valid = True
    for rule in required_ordering:
        before = _first_matching_step_index(
            steps,
            dependency_files,
            required_source_files,
            rule.get("before", ""),
        )
        after = _first_matching_step_index(
            steps,
            dependency_files,
            required_source_files,
            rule.get("after", ""),
        )
        if before is None or after is None:
            valid = False
            violations.append({
                "code": "required_ordering_target_missing",
                "severity": "error",
                "ordering": rule,
                "message": "Required ordering references a step class not present in the plan.",
            })
            continue
        if before > after:
            valid = False
            violations.append({
                "code": "required_ordering_violated",
                "severity": "error",
                "ordering": rule,
                "message": "Plan violates a required ordering constraint.",
            })
    return valid


def _first_matching_step_index(
    steps: list[dict[str, Any]],
    dependency_files: set[str],
    required_source_files: set[str],
    target: str,
) -> int | None:
    for index, step in enumerate(steps):
        allowed_files = _step_allowed_files(step)
        if target == "dependency_update" and allowed_files.intersection(dependency_files):
            return index
        if target == "source_migration" and allowed_files.intersection(required_source_files):
            return index
        if target.startswith("file:") and target.removeprefix("file:") in allowed_files:
            return index
        if target.startswith("symbol:"):
            symbol = target.removeprefix("symbol:")
            if symbol in (step.get("allowed_symbols", []) or []):
                return index
    return None


def _validate_human_review(
    diagnosis: dict[str, Any],
    expected: dict[str, Any],
    violations: list[dict[str, Any]],
) -> bool:
    if "human_review_required" not in expected:
        return True
    expected_value = bool(expected["human_review_required"])
    actual_value = bool(diagnosis.get("human_review_required"))
    if expected_value != actual_value:
        violations.append({
            "code": "human_review_mismatch",
            "severity": "error",
            "expected": expected_value,
            "actual": actual_value,
            "message": "Planner human_review_required does not match expected planner contract.",
        })
        return False
    return True


def _validate_duplicate_scopes(
    steps: list[dict[str, Any]], violations: list[dict[str, Any]]
) -> int:
    seen: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    duplicate_count = 0
    for step in steps:
        scope = (
            tuple(sorted(_step_allowed_files(step))),
            tuple(sorted(step.get("allowed_symbols", []) or [])),
        )
        if scope in seen:
            duplicate_count += 1
            violations.append({
                "code": "duplicate_step_scope",
                "severity": "error",
                "step_id": step.get("step_id"),
                "message": "Two migration steps declare the same file/symbol scope.",
            })
        seen.add(scope)
    return duplicate_count


def _step_matches_group(step: dict[str, Any], group: dict[str, Any]) -> bool:
    required_files = set(
        group.get("required_allowed_files")
        or group.get("allowed_files")
        or group.get("files")
        or []
    )
    if required_files and not required_files.issubset(_step_allowed_files(step)):
        return False

    file_options = group.get("allowed_files_any_of") or []
    if file_options:
        step_files = _step_allowed_files(step)
        if not any(set(option).issubset(step_files) for option in file_options):
            return False

    required_symbols = set(group.get("required_symbols") or [])
    if required_symbols:
        step_symbols = set(step.get("allowed_symbols", []) or [])
        if step_symbols and not required_symbols.issubset(step_symbols):
            return False

    symbol_options = group.get("allowed_symbols_any_of") or []
    if symbol_options:
        step_symbols = set(step.get("allowed_symbols", []) or [])
        if not any(set(option).issubset(step_symbols) for option in symbol_options):
            return False
    return True


def _step_allowed_files(step: dict[str, Any]) -> set[str]:
    return set(step.get("allowed_files", []) or [])


def _step_declared_paths(step: dict[str, Any]) -> set[str]:
    paths = set(step.get("allowed_files", []) or [])
    paths.update(step.get("files", []) or [])
    if step.get("file"):
        paths.add(step["file"])
    return paths


def _add_each(
    violations: list[dict[str, Any]],
    code: str,
    severity: str,
    values: list[str],
    message: str,
) -> None:
    for value in values:
        violations.append({
            "code": code,
            "severity": severity,
            "path": value,
            "message": message,
        })


def _coverage_rate(required: set[str], observed: set[str]) -> float:
    if not required:
        return 1.0
    return len(required.intersection(observed)) / len(required)


def _precision_rate(observed: set[str], allowed: set[str]) -> float:
    if not observed:
        return 1.0
    return len(observed.intersection(allowed)) / len(observed)


def _has_contract_error(violations: list[dict[str, Any]]) -> bool:
    return any(
        violation.get("severity") == "error"
        and violation.get("code")
        in {
            "missing_plan_field",
            "invalid_migration_steps_type",
            "invalid_step_type",
            "missing_step_id",
            "duplicate_step_id",
            "missing_step_allowed_files",
            "unsafe_plan_path",
            "test_file_in_migration_scope",
        }
        for violation in violations
    )


def _is_safe_relative_path(path: str) -> bool:
    return (
        isinstance(path, str)
        and bool(path)
        and not path.startswith("/")
        and "\\" not in path
        and ".." not in path.split("/")
    )


def _is_test_path(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1] if parts else path
    return (
        bool({"test", "tests", "testing"}.intersection(parts))
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )
