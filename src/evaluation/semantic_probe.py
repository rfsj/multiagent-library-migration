from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class ReviewRunner(Protocol):
    def review(
        self,
        *,
        rel_file: Path,
        original_code: str,
        migrated_code: str,
        planned_step: dict[str, Any],
        dataframe_flow_analysis: dict[str, Any],
        logs_dir: Path,
        log_suffix: str = "implementation_review",
    ) -> dict[str, Any]: ...


def run_semantic_probe(
    *,
    review_agent: ReviewRunner,
    diagnosis: dict[str, Any] | None,
    before_dir: Path,
    project_dir: Path,
    accepted_step_ids: list[str],
    logs_dir: Path,
) -> list[dict[str, Any]]:
    """Post-validation false-positive probe.

    Runs the implementation review *once per accepted, migrated file* — after tests
    are already green — to surface semantic regressions that the test suite is too
    weak to catch (e.g. dropped null rows in a pivot, changed sort order). It never
    blocks and never triggers a retry: each flagged file becomes a ``semantic_risk``
    entry that feeds the ``false_positives`` research metric. See
    ``ai_docs/proposal-fuse-judges.md``.
    """
    if not diagnosis:
        return []

    flow_analysis = diagnosis.get("dataframe_flow_analysis", {})
    steps_by_id = {
        step["step_id"]: step for step in diagnosis.get("migration_steps", [])
    }

    risks: list[dict[str, Any]] = []
    for step_id in accepted_step_ids:
        step = steps_by_id.get(step_id)
        if not step:
            continue
        for rel in step.get("files") or ([step["file"]] if step.get("file") else []):
            rel_path = Path(rel)
            if rel_path.suffix != ".py":
                continue
            before = before_dir / rel_path
            after = project_dir / rel_path
            if not before.exists() or not after.exists():
                continue
            original = before.read_text(encoding="utf-8")
            migrated = after.read_text(encoding="utf-8")
            if original == migrated:
                continue

            review = review_agent.review(
                rel_file=rel_path,
                original_code=original,
                migrated_code=migrated,
                planned_step=step,
                dataframe_flow_analysis=flow_analysis,
                logs_dir=logs_dir,
                log_suffix="semantic_probe",
            )
            # A structured-output failure is an inconclusive probe, not evidence of a
            # semantically wrong migration — don't let it inflate false_positives.
            if review.get("structured_output_error"):
                continue
            if review.get("status") == "needs_revision":
                risks.append(
                    {
                        "step_id": step_id,
                        "file": str(rel_path),
                        "confidence": review.get("confidence"),
                        "issues": review.get("issues", []),
                    }
                )
    return risks
