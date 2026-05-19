from __future__ import annotations

from typing import Any


def build_metrics(
    tests_before: dict[str, Any],
    tests_after: dict[str, Any],
    final_validation: dict[str, Any],
) -> dict[str, Any]:
    unmigrated = final_validation["unmigrated_uses"]
    return {
        "tests_before": tests_before["status"],
        "tests_after": tests_after["status"],
        "old_imports_remaining": final_validation["old_imports_remaining"],
        "correctly_migrated_uses": 0 if unmigrated else 1,
        "unmigrated_uses": unmigrated,
        "false_positives": 0,
        "false_negatives": 0,
        "transformation_errors": 0 if final_validation["status"] == "approved" else 1,
        "out_of_scope_changes": final_validation["out_of_scope_changes"],
        "status": "success"
        if tests_before["passed"] and tests_after["passed"] and final_validation["status"] == "approved"
        else "failed",
    }
