from src.agents.validation_agent import (
    LLM_VERDICT_ESCALATE_AFTER,
    ValidationAgent,
    ValidationVerdict,
)
from src.evaluation.metrics import build_metrics


class FakeVerdictChain:
    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = 0

    def invoke(self, payload):
        self.calls += 1
        return self.verdict


def _rejected_evidence():
    return {"status": "rejected", "pytest_feedback": "AssertionError: boom"}


def test_first_rejection_stays_deterministic(tmp_path):
    agent = ValidationAgent()
    agent._chain = FakeVerdictChain(None)  # must not be touched before escalation

    verdict = agent.evaluate_step(
        planned_step={"step_id": "step_001"},
        migration_result={"changed": True},
        before_snapshot={},
        validation_evidence=_rejected_evidence(),
        logs_dir=tmp_path,
        retry_count=0,
    )

    assert verdict["verdict"] == "rejected_implementation"
    assert agent._chain.calls == 0


def test_non_converging_rejection_escalates_and_can_replan(tmp_path):
    agent = ValidationAgent()
    agent._chain = FakeVerdictChain(
        ValidationVerdict(
            step_id="step_001",
            verdict="rejected_plan",
            rationale="The implementation is fine; the plan scoped the step wrong.",
            feedback_target="agent_1",
            feedback_for_agent="Re-plan: migrate the upstream producer first.",
            retry_recommendation="retry",
            confidence="high",
        )
    )

    verdict = agent.evaluate_step(
        planned_step={"step_id": "step_001"},
        migration_result={"changed": True},
        before_snapshot={},
        validation_evidence=_rejected_evidence(),
        logs_dir=tmp_path,
        retry_count=LLM_VERDICT_ESCALATE_AFTER,
    )

    assert verdict["verdict"] == "rejected_plan"
    assert verdict["feedback_target"] == "agent_1"
    assert agent._chain.calls == 1


def test_escalation_falls_back_when_no_structured_output(tmp_path):
    agent = ValidationAgent()
    agent._chain = FakeVerdictChain(None)

    verdict = agent.evaluate_step(
        planned_step={"step_id": "step_001"},
        migration_result={"changed": True},
        before_snapshot={},
        validation_evidence=_rejected_evidence(),
        logs_dir=tmp_path,
        retry_count=LLM_VERDICT_ESCALATE_AFTER,
    )

    assert agent._chain.calls == 1
    assert verdict["verdict"] == "rejected_implementation"


def test_false_negatives_count_rejected_plan_steps():
    metrics = build_metrics(
        tests_before={"status": "passed", "passed": True},
        tests_after={"status": "passed", "passed": True},
        final_validation={
            "unmigrated_uses": 0,
            "old_imports_remaining": 0,
            "out_of_scope_changes": 0,
            "status": "approved",
        },
        retry_counts={"step_001": 2},
        semantic_risks=[],
        verdicts=[
            {"step_id": "step_001", "verdict": "rejected_plan"},
            {"step_id": "step_001", "verdict": "accepted"},
            {"step_id": "step_002", "verdict": "accepted"},
        ],
    )

    assert metrics["false_negatives"] == 1
    assert metrics["false_positives"] == 0
