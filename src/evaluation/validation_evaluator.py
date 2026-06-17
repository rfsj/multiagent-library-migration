from __future__ import annotations

from typing import Any


def evaluate_validation_result(
    *,
    validation_report: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare ValidationAgent output with a task-level validation oracle.

    If no explicit ``validation_oracle`` exists, the function still returns the
    observed decision and detected reasons, but marks decision-correctness as
    unknown. This keeps exploratory runs useful without pretending that the
    Validation Agent was evaluated against an independent label.
    """
    metadata = metadata or {}
    oracle = metadata.get("validation_oracle") or {}
    final_validation = validation_report.get("final_validation", {}) or {}
    tests = validation_report.get("tests", {}) or {}

    observed_verdict = (
        "approved"
        if tests.get("passed") and final_validation.get("status") == "approved"
        else "rejected"
    )
    observed_reasons = _observed_reasons(tests, final_validation)

    expected_verdict = oracle.get("expected_verdict")
    expected_reasons = set(oracle.get("expected_rejection_reasons") or [])
    must_detect = set(oracle.get("must_detect") or [])

    decision_correct = (
        None if expected_verdict not in {"approved", "rejected"}
        else observed_verdict == expected_verdict
    )
    reason_match = (
        None
        if not expected_reasons
        else expected_reasons.issubset(set(observed_reasons))
    )
    must_detect_match = (
        None
        if not must_detect
        else must_detect.issubset(set(observed_reasons) | set(_observed_evidence(final_validation)))
    )

    false_accept = expected_verdict == "rejected" and observed_verdict == "approved"
    false_reject = expected_verdict == "approved" and observed_verdict == "rejected"

    return {
        "oracle_available": bool(oracle),
        "expected_verdict": expected_verdict,
        "observed_verdict": observed_verdict,
        "validation_decision_correct": decision_correct,
        "false_accept": false_accept,
        "false_reject": false_reject,
        "expected_rejection_reasons": sorted(expected_reasons),
        "observed_rejection_reasons": observed_reasons,
        "rejection_reason_match": reason_match,
        "must_detect": sorted(must_detect),
        "must_detect_match": must_detect_match,
        "tests_passed": bool(tests.get("passed")),
        "final_validation_status": final_validation.get("status"),
        "out_of_scope_changes": final_validation.get("out_of_scope_changes"),
        "old_imports_remaining": final_validation.get("old_imports_remaining"),
        "unmigrated_uses": final_validation.get("unmigrated_uses"),
    }


def _observed_reasons(
    tests: dict[str, Any], final_validation: dict[str, Any]
) -> list[str]:
    reasons = []
    if not tests.get("passed"):
        reasons.append("pytest_failed")
    if (final_validation.get("out_of_scope_changes") or 0) > 0:
        reasons.append("out_of_scope_change")
    if (final_validation.get("old_imports_remaining") or 0) > 0:
        reasons.append("old_imports_remaining")
    if (final_validation.get("unmigrated_uses") or 0) > 0:
        reasons.append("unmigrated_uses")
    if final_validation.get("status") == "rejected" and not reasons:
        reasons.append("validation_rejected")
    return reasons


def _observed_evidence(final_validation: dict[str, Any]) -> list[str]:
    evidence = []
    evidence.extend(final_validation.get("out_of_scope_files") or [])
    evidence.extend(final_validation.get("allowed_files") or [])
    return [str(item) for item in evidence]
