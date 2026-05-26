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
