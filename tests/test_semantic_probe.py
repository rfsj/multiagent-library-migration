from pathlib import Path

from src.agents.implementation_review_agent import ImplementationReviewAgent
from src.evaluation.semantic_probe import run_semantic_probe


class FakeReviewAgent:
    """Flags files whose migrated code contains the marker FLAG_ME."""

    def __init__(self):
        self.calls = []

    def review(
        self,
        *,
        rel_file,
        original_code,
        migrated_code,
        planned_step,
        dataframe_flow_analysis,
        logs_dir,
        log_suffix="implementation_review",
    ):
        self.calls.append({"file": str(rel_file), "log_suffix": log_suffix})
        flagged = "FLAG_ME" in migrated_code
        return {
            "agent": "implementation_review_agent",
            "step_id": planned_step["step_id"],
            "file": str(rel_file),
            "status": "needs_revision" if flagged else "approved",
            "issues": [{"kind": "semantic", "explanation": "pivot drops null rows"}]
            if flagged
            else [],
            "confidence": "high",
        }


def _make_dirs(tmp_path, before_src, after_src):
    before_dir = tmp_path / "before"
    project_dir = tmp_path / "project"
    for base, src in ((before_dir, before_src), (project_dir, after_src)):
        (base / "src").mkdir(parents=True)
        (base / "src" / "p.py").write_text(src, encoding="utf-8")
    return before_dir, project_dir


def _diagnosis():
    return {
        "dataframe_flow_analysis": {"symbols": []},
        "migration_steps": [
            {"step_id": "step_001", "file": "src/p.py", "allowed_files": ["src/p.py"]}
        ],
    }


def test_probe_flags_accepted_file_with_semantic_risk(tmp_path):
    before_dir, project_dir = _make_dirs(
        tmp_path, "import pandas as pd\n", "import polars as pl  # FLAG_ME\n"
    )
    review_agent = FakeReviewAgent()

    risks = run_semantic_probe(
        review_agent=review_agent,
        diagnosis=_diagnosis(),
        before_dir=before_dir,
        project_dir=project_dir,
        accepted_step_ids=["step_001"],
        logs_dir=tmp_path / "logs",
    )

    assert len(risks) == 1
    assert risks[0]["file"] == "src/p.py"
    assert risks[0]["issues"][0]["kind"] == "semantic"
    assert review_agent.calls[0]["log_suffix"] == "semantic_probe"


def test_probe_skips_unchanged_and_clean_files(tmp_path):
    # Unchanged file -> not reviewed at all; changed-but-clean -> reviewed, no risk.
    before_dir, project_dir = _make_dirs(
        tmp_path, "import pandas as pd\n", "import polars as pl\n"
    )
    review_agent = FakeReviewAgent()

    risks = run_semantic_probe(
        review_agent=review_agent,
        diagnosis=_diagnosis(),
        before_dir=before_dir,
        project_dir=project_dir,
        accepted_step_ids=["step_001"],
        logs_dir=tmp_path / "logs",
    )

    assert risks == []
    assert len(review_agent.calls) == 1  # changed file was reviewed, found clean


class StructuredOutputFailureReviewAgent:
    def review(self, *, rel_file, planned_step, **kwargs):
        return {
            "agent": "implementation_review_agent",
            "step_id": planned_step["step_id"],
            "file": str(rel_file),
            "status": "needs_revision",
            "structured_output_error": "no structured output",
            "issues": [{"kind": "structured_output_missing"}],
            "confidence": "low",
        }


def test_probe_ignores_structured_output_failures(tmp_path):
    # An inconclusive probe (review could not produce structured output) must not
    # be counted as a semantic risk / false positive.
    before_dir, project_dir = _make_dirs(
        tmp_path, "import pandas as pd\n", "import polars as pl\n"
    )

    risks = run_semantic_probe(
        review_agent=StructuredOutputFailureReviewAgent(),
        diagnosis=_diagnosis(),
        before_dir=before_dir,
        project_dir=project_dir,
        accepted_step_ids=["step_001"],
        logs_dir=tmp_path / "logs",
    )

    assert risks == []


def test_probe_only_reviews_accepted_steps(tmp_path):
    before_dir, project_dir = _make_dirs(
        tmp_path, "import pandas as pd\n", "import polars as pl  # FLAG_ME\n"
    )
    review_agent = FakeReviewAgent()

    risks = run_semantic_probe(
        review_agent=review_agent,
        diagnosis=_diagnosis(),
        before_dir=before_dir,
        project_dir=project_dir,
        accepted_step_ids=[],  # nothing accepted -> nothing probed
        logs_dir=tmp_path / "logs",
    )

    assert risks == []
    assert review_agent.calls == []


def test_implementation_review_timeout_falls_back(tmp_path, monkeypatch):
    class TimeoutChain:
        def invoke(self, payload):
            raise TimeoutError("request timed out")

    def fake_init(self):
        self._chain = TimeoutChain()

    monkeypatch.setattr(ImplementationReviewAgent, "__init__", fake_init)
    agent = ImplementationReviewAgent()

    payload = agent.review(
        rel_file=Path("src/p.py"),
        original_code="import pandas as pd\n",
        migrated_code="import polars as pl\n",
        planned_step={"step_id": "step_001", "file": "src/p.py"},
        dataframe_flow_analysis={"symbols": []},
        logs_dir=tmp_path / "logs",
    )

    assert payload["status"] == "needs_revision"
    assert "structured output" in payload["structured_output_error"].lower()
