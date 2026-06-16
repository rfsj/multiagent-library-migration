from __future__ import annotations

from typing import Any


def build_metrics(
    tests_before: dict[str, Any],
    tests_after: dict[str, Any],
    final_validation: dict[str, Any],
    retry_counts: dict[str, int] | None = None,
    semantic_risks: list[dict[str, Any]] | None = None,
    verdicts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    unmigrated = final_validation["unmigrated_uses"]
    retries = retry_counts or {}
    correctly_migrated_uses = None if unmigrated is None else (0 if unmigrated else 1)
    # Sobre false_positives e false_negatives: ambos são sinais de pesquisa, não
    # números exatos. O false_positive é o palpite do ImplementationReviewAgent
    # (varia entre runs; falhas de structured output já são filtradas e não contam).
    # O false_negative é a atribuição do juiz religado. Ambos são bons pra medir
    # tendência (qual modelo gera mais migração semanticamente suspeita, com que
    # frequência o plano é culpado), não pra cravar um número exato.
    #
    # A false negative is a step the framework rejected even though the LLM verdict
    # attributed the failure to the plan, not the migration (rejected_plan) — i.e.
    # the migrated code was judged correct but the step was still rejected.
    false_negatives = len(
        {
            verdict["step_id"]
            for verdict in (verdicts or [])
            if verdict.get("verdict") == "rejected_plan"
        }
    )
    return {
        "tests_before": tests_before["status"],
        "tests_after": tests_after["status"],
        "old_imports_remaining": final_validation["old_imports_remaining"],
        "correctly_migrated_uses": correctly_migrated_uses,
        "unmigrated_uses": unmigrated,
        # A false positive is a migration the framework accepted (tests green) but
        # the post-validation semantic probe flagged as semantically wrong.
        "false_positives": len(semantic_risks or []),
        "false_negatives": false_negatives,
        "transformation_errors": 0 if final_validation["status"] == "approved" else 1,
        "out_of_scope_changes": final_validation["out_of_scope_changes"],
        "total_retries": sum(retries.values()),
        "status": "success"
        if tests_before["passed"]
        and tests_after["passed"]
        and final_validation["status"] == "approved"
        else "failed",
    }
