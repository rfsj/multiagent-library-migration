from __future__ import annotations

from pathlib import Path

from src.graph.state import WorkflowState
from src.graph.workflow import run_simple_workflow


class FakeDiagnosisAgent:
    def run(
        self,
        project_dir: Path,
        logs_dir: Path,
        source_library: str,
        target_library: str,
        replan_feedback: dict | None = None,
        replan_attempt: int = 0,
    ):
        return {
            "agent": "diagnosis_agent",
            "source_library": source_library,
            "target_library": target_library,
            "read_only": True,
            "dependency_files": ["requirements.txt"],
            "affected_files": ["src/example.py"],
            "related_tests": [],
            "complexity": {"src/example.py": "low"},
            "migration_steps": [
                {
                    "step_id": "step_001",
                    "file": "src/example.py",
                    "description": "Migrate example file.",
                    "allowed_files": ["src/example.py"],
                    "status": "planned",
                }
            ],
        }


class FakeMigrationAgent:
    def run_step(self, project_dir: Path, step: dict, logs_dir: Path):
        target = project_dir / step["file"]
        target.write_text("import polars as pl\n", encoding="utf-8")
        return {
            "agent": "migration_agent",
            "step_id": step["step_id"],
            "file": step["file"],
            "changed": True,
            "status": "completed",
        }


class RepairAwareFakeMigrationAgent(FakeMigrationAgent):
    retry_feedback_seen = []

    def run_step(self, project_dir: Path, step: dict, logs_dir: Path):
        self.retry_feedback_seen.append(step.get("retry_feedback"))
        return super().run_step(project_dir, step, logs_dir)


class FakeValidationAgent:
    def validate_step(self, project_dir: Path, step: dict, before_dir: Path, logs_dir: Path):
        assert before_dir.exists()
        return {
            "agent": "validation_agent",
            "step_id": step["step_id"],
            "changed_files": [step["file"]],
            "out_of_scope_changes": [],
            "tests": "passed",
            "status": "approved",
        }

    def evaluate_step(
        self,
        planned_step: dict,
        migration_result: dict,
        before_snapshot: dict,
        validation_evidence: dict,
        logs_dir: Path,
    ):
        return {
            "agent": "validation_agent",
            "step_id": planned_step["step_id"],
            "verdict": "accepted",
            "rationale": "Step passed fake validation.",
            "feedback_target": "none",
            "feedback_for_agent": "",
            "retry_recommendation": "not_needed",
            "confidence": "high",
        }


class TwoStepDiagnosisAgent:
    def run(
        self,
        project_dir: Path,
        logs_dir: Path,
        source_library: str,
        target_library: str,
        replan_feedback: dict | None = None,
        replan_attempt: int = 0,
    ):
        return {
            "agent": "diagnosis_agent",
            "source_library": source_library,
            "target_library": target_library,
            "read_only": True,
            "dependency_files": [],
            "affected_files": ["src/one.py", "src/two.py"],
            "related_tests": [],
            "complexity": {"src/one.py": "low", "src/two.py": "low"},
            "migration_steps": [
                {
                    "step_id": "step_001",
                    "file": "src/one.py",
                    "description": "Migrate first file.",
                    "allowed_files": ["src/one.py"],
                    "status": "planned",
                },
                {
                    "step_id": "step_002",
                    "file": "src/two.py",
                    "description": "Migrate second file.",
                    "allowed_files": ["src/two.py"],
                    "status": "planned",
                },
            ],
        }


class RejectFirstStepValidationAgent(FakeValidationAgent):
    def evaluate_step(
        self,
        planned_step: dict,
        migration_result: dict,
        before_snapshot: dict,
        validation_evidence: dict,
        logs_dir: Path,
    ):
        if planned_step["step_id"] == "step_001":
            return {
                "agent": "validation_agent",
                "step_id": planned_step["step_id"],
                "verdict": "rejected_implementation",
                "rationale": "Step failed fake validation.",
                "feedback_target": "agent_2",
                "feedback_for_agent": "try again",
                "retry_recommendation": "retry",
                "confidence": "high",
            }
        return super().evaluate_step(
            planned_step,
            migration_result,
            before_snapshot,
            validation_evidence,
            logs_dir,
        )


class RepairableRejectValidationAgent(FakeValidationAgent):
    def validate_step(self, project_dir: Path, step: dict, before_dir: Path, logs_dir: Path):
        return {
            "agent": "validation_agent",
            "step_id": step["step_id"],
            "changed_files": [step["file"]],
            "out_of_scope_changes": [],
            "tests": "failed",
            "pytest_feedback": "TypeError: DataFrame object does not support `Series` assignment by index",
            "status": "rejected",
        }

    def evaluate_step(
        self,
        planned_step: dict,
        migration_result: dict,
        before_snapshot: dict,
        validation_evidence: dict,
        logs_dir: Path,
    ):
        return {
            "agent": "validation_agent",
            "step_id": planned_step["step_id"],
            "verdict": "rejected_implementation",
            "rationale": "Step failed fake validation.",
            "feedback_target": "agent_2",
            "feedback_for_agent": "raw validation feedback",
            "retry_recommendation": "retry",
            "confidence": "high",
        }


class FakeRepairAgent:
    def __init__(self):
        self.calls = []

    def build_repair_plan(
        self,
        *,
        project_dir: Path,
        planned_step: dict,
        migration_result: dict,
        validation_evidence: dict,
        logs_dir: Path,
        attempt: int,
    ):
        self.calls.append(
            {
                "step_id": planned_step["step_id"],
                "attempt": attempt,
                "pytest_feedback": validation_evidence["pytest_feedback"],
            }
        )
        return {
            "agent": "repair_agent",
            "step_id": planned_step["step_id"],
            "file": planned_step["file"],
            "attempt": attempt,
            "failure_category": "unsupported_operation",
            "root_cause": "Polars column assignment used pandas syntax.",
            "repair_strategy": "replace_assignment_with_with_columns",
            "instructions_for_migration_agent": [
                "Replace df column assignment with with_columns.",
            ],
            "acceptance_criteria": [
                "No migrated Polars DataFrame uses df['col'] assignment.",
            ],
            "must_not_do": ["Do not use df['col'] = ... on Polars DataFrames."],
            "confidence": "high",
        }


def test_workflow_runs_migration_step_through_langgraph(tmp_path, monkeypatch):
    monkeypatch.setattr("src.graph.workflow.DiagnosisAgent", FakeDiagnosisAgent)
    monkeypatch.setattr("src.graph.workflow.MigrationAgent", FakeMigrationAgent)
    monkeypatch.setattr("src.graph.workflow.ValidationAgent", FakeValidationAgent)

    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "example.py").write_text("import pandas as pd\n", encoding="utf-8")

    state = WorkflowState(
        task_id="task_fake",
        project_dir=project_dir,
        run_dir=tmp_path / "run",
        source_library="pandas",
        target_library="polars",
    )

    result = run_simple_workflow(state)

    assert result.diagnosis is not None
    assert result.migrations == [
        {
            "agent": "migration_agent",
            "step_id": "step_001",
            "file": "src/example.py",
            "changed": True,
            "status": "completed",
        }
    ]
    assert result.validations[0]["status"] == "approved"
    assert result.verdicts[0]["verdict"] == "accepted"
    assert (project_dir / "src" / "example.py").read_text(encoding="utf-8") == "import polars as pl\n"
    assert (tmp_path / "run" / "snapshots" / "before_step_001" / "src" / "example.py").exists()


def test_workflow_continues_after_step_exhausts_retries(tmp_path, monkeypatch):
    monkeypatch.setattr("src.graph.workflow.DiagnosisAgent", TwoStepDiagnosisAgent)
    monkeypatch.setattr("src.graph.workflow.MigrationAgent", FakeMigrationAgent)
    monkeypatch.setattr("src.graph.workflow.ValidationAgent", RejectFirstStepValidationAgent)

    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "one.py").write_text("import pandas as pd\n", encoding="utf-8")
    (source_dir / "two.py").write_text("import pandas as pd\n", encoding="utf-8")

    state = WorkflowState(
        task_id="task_fake",
        project_dir=project_dir,
        run_dir=tmp_path / "run",
        source_library="pandas",
        target_library="polars",
    )

    result = run_simple_workflow(state)

    assert result.abort_reason is None
    assert result.retry_counts == {"step_001": 3}
    assert result.failed_steps[0]["step_id"] == "step_001"
    assert result.failed_steps[0]["manual_review_files"] == ["src/one.py"]
    assert [migration["step_id"] for migration in result.migrations] == [
        "step_001",
        "step_001",
        "step_001",
        "step_002",
    ]
    assert result.verdicts[-1]["step_id"] == "step_002"
    assert result.verdicts[-1]["verdict"] == "accepted"
    failed_content = (source_dir / "one.py").read_text(encoding="utf-8")
    assert failed_content.startswith("import pandas as pd\n")
    assert "MIGRATION MANUAL REVIEW START step_001" in failed_content
    assert "Step failed fake validation." in failed_content
    assert (source_dir / "two.py").read_text(encoding="utf-8") == "import polars as pl\n"


def test_workflow_uses_repair_agent_feedback_for_retry(tmp_path, monkeypatch):
    fake_repair_agent = FakeRepairAgent()
    RepairAwareFakeMigrationAgent.retry_feedback_seen = []
    monkeypatch.setattr("src.graph.workflow.DiagnosisAgent", FakeDiagnosisAgent)
    monkeypatch.setattr("src.graph.workflow.MigrationAgent", RepairAwareFakeMigrationAgent)
    monkeypatch.setattr("src.graph.workflow.ValidationAgent", RepairableRejectValidationAgent)
    monkeypatch.setattr("src.graph.workflow.RepairAgent", lambda: fake_repair_agent)

    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "example.py").write_text("import pandas as pd\n", encoding="utf-8")

    state = WorkflowState(
        task_id="task_fake",
        project_dir=project_dir,
        run_dir=tmp_path / "run",
        source_library="pandas",
        target_library="polars",
    )

    result = run_simple_workflow(state)

    assert fake_repair_agent.calls
    assert result.retry_counts == {"step_001": 3}
    retry_feedback = [feedback for feedback in RepairAwareFakeMigrationAgent.retry_feedback_seen if feedback]
    assert retry_feedback
    assert "RepairAgent produced a repair plan" in retry_feedback[0]["feedback_for_agent"]
    assert "Failure category: unsupported_operation" in retry_feedback[0]["feedback_for_agent"]
    assert "Acceptance criteria" in retry_feedback[0]["feedback_for_agent"]
    assert "No migrated Polars DataFrame uses df['col'] assignment." in retry_feedback[0]["feedback_for_agent"]
    assert result.failed_steps[0]["step_id"] == "step_001"
