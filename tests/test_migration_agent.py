import json
import re
from types import SimpleNamespace

import pytest

from src.agents.implementation_review_agent import ImplementationReviewAgent
from src.agents.migration_agent import MigrationAgent, _retry_feedback_context
from src.agents.repair_agent import RepairAgent
from src.agents.validation_agent import _actionable_validation_feedback
from src.migration_config import MigrationConfig


class FakeMigrationChain:
    def __init__(self, migrated_versions):
        self.migrated_versions = list(migrated_versions)
        self.calls = []

    def invoke(self, payload):
        self.calls.append(payload)
        return SimpleNamespace(migrated_code=self.migrated_versions.pop(0))


class RuleBasedMigrationChain:
    def invoke(self, payload):
        return SimpleNamespace(migrated_code=rule_based_migrate(payload["source_code"]))


class FakeReviewChain:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def invoke(self, payload):
        self.calls.append(payload)
        return self.results.pop(0)


def rule_based_migrate(source):
    output = source
    output = re.sub(r"^import pandas as pd$", "import polars as pl", output, flags=re.MULTILINE)
    output = output.replace("pd.DataFrame(", "pl.DataFrame(")
    output = output.replace("pd.Series(", "pl.Series(")
    output = output.replace("pd.concat(", "pl.concat(")
    output = output.replace("pd.read_csv(", "pl.read_csv(")
    output = output.replace("pd.read_json(", "pl.read_json(")
    output = output.replace('.to_dict("records")', ".to_dicts()")
    output = output.replace(".to_dict('records')", ".to_dicts()")
    output = re.sub(r"\.groupby\(", ".group_by(", output)
    output = re.sub(r"\.drop_duplicates\(", ".unique(", output)
    output = re.sub(r"\.sort_values\(", ".sort(", output)
    output = re.sub(r"\.reset_index\(drop=True\)", "", output)
    return output


@pytest.fixture(autouse=True)
def fake_migration_agent_llm(monkeypatch):
    def fake_init(self):
        self._chain = RuleBasedMigrationChain()
        self._current_unmigrated_patterns = []

    monkeypatch.setattr(MigrationAgent, "__init__", fake_init)


def test_dependency_step_updates_only_requirements(tmp_path):
    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    logs_dir = tmp_path / "logs"
    source_dir.mkdir(parents=True)
    requirements = project_dir / "requirements.txt"
    python_file = source_dir / "processing.py"
    requirements.write_text("pandas==2.2.3\npytest==8.3.4\n", encoding="utf-8")
    python_file.write_text("import pandas as pd\n", encoding="utf-8")

    result = MigrationAgent().run_step(
        project_dir,
        {
            "step_id": "step_001",
            "file": "requirements.txt",
            "allowed_files": ["requirements.txt"],
            "target_library": "polars",
        },
        logs_dir,
    )

    assert result["status"] == "completed"
    assert requirements.read_text(encoding="utf-8") == "pandas==2.2.3\npytest==8.3.4\npolars\n"
    assert python_file.read_text(encoding="utf-8") == "import pandas as pd\n"
    assert json.loads((logs_dir / "step_001_migration.json").read_text(encoding="utf-8")) == result


def test_validation_feedback_identifies_semantic_ordering_repairs():
    feedback = _actionable_validation_feedback(
        {
            "pytest_feedback": (
                "E       AssertionError: assert [{'entity': 'B', 'score': 1}] "
                "== [{'entity': 'A', 'score': 9}]\n"
                "E         At index 0 diff: {'entity': 'B', 'score': 1} "
                "!= {'entity': 'A', 'score': 9}\n"
                "E       AssertionError: assert ['index_col', 'z_col', 'a_col'] "
                "== ['index_col', 'a_col', 'z_col']\n"
                "E         At index 1 diff: 'z_col' != 'a_col'\n"
                "E         At index 0 diff: {'index_col': None, 'a_col': 40.0} "
                "!= {'index_col': 'group-1', 'a_col': 80.0}"
            )
        }
    )

    assert "sort(..., descending=...)" in feedback
    assert "maintain_order=True" in feedback
    assert "column-order mismatch" in feedback
    assert "filter null values" in feedback


def test_migration_retry_context_includes_structured_repair_plan():
    context = _retry_feedback_context(
        {
            "feedback_for_agent": "RepairAgent produced a repair plan.",
            "repair_plan": {
                "failure_category": "semantic_equivalence_error",
                "instructions_for_migration_agent": ["Fix sort order."],
                "acceptance_criteria": ["No sort call uses ascending=."],
                "must_not_do": ["Do not use pandas APIs."],
            },
            "validation_feedback": "pytest failed",
        }
    )

    assert "Structured Repair Plan" in context
    assert "Mandatory Acceptance Criteria" in context
    assert "No sort call uses ascending=." in context
    assert "Forbidden Patterns For This Retry" in context
    assert "Do not use pandas APIs." in context


def test_python_step_does_not_update_requirements(tmp_path):
    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    logs_dir = tmp_path / "logs"
    source_dir.mkdir(parents=True)
    requirements = project_dir / "requirements.txt"
    python_file = source_dir / "processing.py"
    requirements.write_text("pandas==2.2.3\npytest==8.3.4\n", encoding="utf-8")
    python_file.write_text(
        'import pandas as pd\n\n'
        "def load(path):\n"
        "    df = pd.read_csv(path)\n"
        '    df = df[df["status"] == "paid"]\n'
        '    return df[["customer_id", "total"]].sort_values("total")\n',
        encoding="utf-8",
    )

    result = MigrationAgent().run_step(
        project_dir,
        {
            "step_id": "step_002",
            "file": "src/processing.py",
            "allowed_files": ["src/processing.py"],
        },
        logs_dir,
    )

    migrated = python_file.read_text(encoding="utf-8")
    assert result["status"] == "completed"
    assert "import polars as pl" in migrated
    assert "pl.read_csv(path)" in migrated
    assert requirements.read_text(encoding="utf-8") == "pandas==2.2.3\npytest==8.3.4\n"


def test_migration_agent_retries_missing_structured_output(tmp_path):
    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    logs_dir = tmp_path / "logs"
    source_dir.mkdir(parents=True)
    python_file = source_dir / "processing.py"
    python_file.write_text("import pandas as pd\n", encoding="utf-8")
    agent = MigrationAgent.__new__(MigrationAgent)
    agent._chain = FakeMigrationChain([None, "import polars as pl\n"])

    result = agent.run_step(
        project_dir,
        {
            "step_id": "step_001",
            "file": "src/processing.py",
            "allowed_files": ["src/processing.py"],
            "source_library": "pandas",
            "target_library": "polars",
        },
        logs_dir,
    )

    assert result["status"] == "completed"
    assert result["structured_output_attempts"] == 2
    assert len(agent._chain.calls) == 2
    assert python_file.read_text(encoding="utf-8") == "import polars as pl\n"


def test_migration_agent_records_no_change_when_structured_output_missing(tmp_path):
    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    logs_dir = tmp_path / "logs"
    source_dir.mkdir(parents=True)
    python_file = source_dir / "processing.py"
    original = "import pandas as pd\n"
    python_file.write_text(original, encoding="utf-8")
    agent = MigrationAgent.__new__(MigrationAgent)
    agent._chain = FakeMigrationChain([None, None])

    result = agent.run_step(
        project_dir,
        {
            "step_id": "step_001",
            "file": "src/processing.py",
            "allowed_files": ["src/processing.py"],
            "source_library": "pandas",
            "target_library": "polars",
        },
        logs_dir,
    )

    log_payload = json.loads(
        (logs_dir / "step_001_migration.json").read_text(encoding="utf-8")
    )
    assert result["status"] == "no_change"
    assert result["structured_output_attempts"] == 2
    assert "no structured output" in result["structured_output_error"]
    assert log_payload["structured_output_error"] == result["structured_output_error"]
    assert python_file.read_text(encoding="utf-8") == original


def test_migration_agent_migrates_grouped_files_in_one_step(tmp_path):
    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    logs_dir = tmp_path / "logs"
    source_dir.mkdir(parents=True)
    (source_dir / "loaders.py").write_text("import pandas as pd\n", encoding="utf-8")
    (source_dir / "summaries.py").write_text("import pandas as pd\n", encoding="utf-8")

    result = MigrationAgent().run_step(
        project_dir,
        {
            "step_id": "step_001",
            "file": "src/loaders.py",
            "files": ["src/loaders.py", "src/summaries.py"],
            "allowed_files": ["src/loaders.py", "src/summaries.py"],
            "source_library": "pandas",
            "target_library": "polars",
        },
        logs_dir,
    )

    assert result["status"] == "completed"
    assert result["changed_files"] == ["src/loaders.py", "src/summaries.py"]
    assert (source_dir / "loaders.py").read_text(encoding="utf-8") == "import polars as pl\n"
    assert (source_dir / "summaries.py").read_text(encoding="utf-8") == "import polars as pl\n"


def test_python_step_updates_requirements_when_allowed(tmp_path):
    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    logs_dir = tmp_path / "logs"
    source_dir.mkdir(parents=True)
    requirements = project_dir / "requirements.txt"
    python_file = source_dir / "processing.py"
    requirements.write_text("pandas==2.2.3\npytest==8.3.4\n", encoding="utf-8")
    python_file.write_text(
        'import pandas as pd\n\n'
        "def load(path):\n"
        "    return pd.read_csv(path)\n",
        encoding="utf-8",
    )

    result = MigrationAgent().run_step(
        project_dir,
        {
            "step_id": "step_001",
            "file": "src/processing.py",
            "allowed_files": ["src/processing.py", "requirements.txt"],
            "target_library": "polars",
        },
        logs_dir,
    )

    assert result["status"] == "completed"
    assert result["changed_files"] == ["src/processing.py", "requirements.txt"]
    assert requirements.read_text(encoding="utf-8") == "pandas==2.2.3\npytest==8.3.4\npolars\n"


def test_step_target_must_be_allowed(tmp_path):
    project_dir = tmp_path / "project"
    logs_dir = tmp_path / "logs"
    project_dir.mkdir()
    (project_dir / "requirements.txt").write_text("pandas==2.2.3\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not listed in allowed_files"):
        MigrationAgent().run_step(
            project_dir,
            {
                "step_id": "step_001",
                "file": "requirements.txt",
                "allowed_files": ["src/processing.py"],
                "target_library": "polars",
            },
            logs_dir,
        )


def test_dependency_step_requires_target_library_from_diagnosis(tmp_path):
    project_dir = tmp_path / "project"
    logs_dir = tmp_path / "logs"
    project_dir.mkdir()
    (project_dir / "requirements.txt").write_text("pandas==2.2.3\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no target_library"):
        MigrationAgent().run_step(
            project_dir,
            {
                "step_id": "step_001",
                "file": "requirements.txt",
                "allowed_files": ["requirements.txt"],
            },
            logs_dir,
        )


def test_dependency_step_preserves_existing_target_version(tmp_path):
    project_dir = tmp_path / "project"
    logs_dir = tmp_path / "logs"
    project_dir.mkdir()
    requirements = project_dir / "requirements.txt"
    requirements.write_text("pandas==2.2.3\npolars==1.17.1\npytest==8.3.4\n", encoding="utf-8")

    result = MigrationAgent().run_step(
        project_dir,
        {
            "step_id": "step_001",
            "file": "requirements.txt",
            "allowed_files": ["requirements.txt"],
            "target_library": "polars",
        },
        logs_dir,
    )

    assert result["status"] == "no_change"
    assert requirements.read_text(encoding="utf-8") == "pandas==2.2.3\npolars==1.17.1\npytest==8.3.4\n"


def test_dependency_step_preserves_require_hashes_mode(monkeypatch, tmp_path):
    project_dir = tmp_path / "project"
    logs_dir = tmp_path / "logs"
    project_dir.mkdir()
    requirements = project_dir / "requirements.txt"
    requirements.write_text(
        "pandas==2.2.3 \\\n"
        "    --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
        encoding="utf-8",
    )

    def fake_hashed_requirement(self, package_name):
        assert package_name == "polars"
        return (
            "polars==1.41.1 \\\n"
            "    --hash=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        )

    monkeypatch.setattr(
        MigrationAgent,
        "_resolve_hashed_requirement",
        fake_hashed_requirement,
    )

    result = MigrationAgent().run_step(
        project_dir,
        {
            "step_id": "step_001",
            "file": "requirements.txt",
            "allowed_files": ["requirements.txt"],
            "target_library": "polars",
        },
        logs_dir,
    )

    migrated = requirements.read_text(encoding="utf-8")
    assert result["status"] == "completed"
    assert "polars\n" not in migrated
    assert "polars==1.41.1 \\" in migrated
    assert "--hash=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" in migrated


def test_generic_dataframe_helpers_migrate_without_project_markers(tmp_path):
    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    logs_dir = tmp_path / "logs"
    source_dir.mkdir(parents=True)
    python_file = source_dir / "helpers.py"
    python_file.write_text(
        "import pandas as pd\n\n\n"
        "def make_frame(rows):\n"
        "    return pd.DataFrame(rows)\n\n\n"
        "def combine(left, right):\n"
        "    return pd.concat([left, right]).to_dict(\"records\")\n",
        encoding="utf-8",
    )

    result = MigrationAgent().run_step(
        project_dir,
        {
            "step_id": "step_001",
            "file": "src/helpers.py",
            "allowed_files": ["src/helpers.py"],
        },
        logs_dir,
    )

    migrated = python_file.read_text(encoding="utf-8")
    assert result["status"] == "completed"
    assert "import pandas" not in migrated
    assert "pd." not in migrated
    assert "pl.DataFrame(rows)" in migrated
    assert "pl.concat([left, right]).to_dicts()" in migrated


def test_generic_file_level_step_applies_supported_dataframe_rewrites(tmp_path):
    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    logs_dir = tmp_path / "logs"
    source_dir.mkdir(parents=True)
    python_file = source_dir / "processing.py"
    python_file.write_text(
        "import pandas as pd\n\n\n"
        "def load_table(path):\n"
        "    return pd.read_csv(path)\n\n\n"
        "def summarize(path):\n"
        "    return load_table(path).groupby('region').sum().reset_index(drop=True)\n\n\n"
        "def latest(path):\n"
        "    return load_table(path).drop_duplicates(subset=['account_id']).sort_values('date')\n",
        encoding="utf-8",
    )

    result = MigrationAgent().run_step(
        project_dir,
        {
            "step_id": "step_001",
            "file": "src/processing.py",
            "allowed_files": ["src/processing.py"],
        },
        logs_dir,
    )

    migrated = python_file.read_text(encoding="utf-8")
    assert result["status"] == "completed"
    assert "import pandas" not in migrated
    assert "pd." not in migrated
    assert "import polars as pl" in migrated
    assert ".group_by(" in migrated
    assert ".unique(" in migrated
    assert ".sort(" in migrated


def test_symbol_step_only_updates_allowed_function(tmp_path):
    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    logs_dir = tmp_path / "logs"
    source_dir.mkdir(parents=True)
    python_file = source_dir / "processing.py"
    python_file.write_text(
        "import pandas as pd\n\n\n"
        "def load(path):\n"
        "    return pd.read_csv(path)\n\n\n"
        "def untouched(path):\n"
        "    return pd.read_csv(path)\n",
        encoding="utf-8",
    )

    result = MigrationAgent().run_step(
        project_dir,
        {
            "step_id": "step_001",
            "file": "src/processing.py",
            "allowed_files": ["src/processing.py"],
            "allowed_symbols": ["load"],
        },
        logs_dir,
    )

    migrated = python_file.read_text(encoding="utf-8")
    assert result["status"] == "completed"
    assert "import pandas as pd" in migrated
    assert "import polars as pl" in migrated
    assert "def load(path):\n    return pl.read_csv(path)" in migrated
    assert "def untouched(path):\n    return pd.read_csv(path)" in migrated


def test_symbol_step_removes_pandas_import_when_no_pd_uses_remain(tmp_path):
    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    logs_dir = tmp_path / "logs"
    source_dir.mkdir(parents=True)
    python_file = source_dir / "processing.py"
    python_file.write_text(
        "import pandas as pd\n\n\n"
        "def load(path):\n"
        "    return pd.read_csv(path)\n",
        encoding="utf-8",
    )

    result = MigrationAgent().run_step(
        project_dir,
        {
            "step_id": "step_001",
            "file": "src/processing.py",
            "allowed_files": ["src/processing.py"],
            "allowed_symbols": ["load"],
        },
        logs_dir,
    )

    migrated = python_file.read_text(encoding="utf-8")
    assert result["status"] == "completed"
    assert "import pandas as pd" not in migrated
    assert "import polars as pl" in migrated
    assert "pd." not in migrated


def _raw_pandas_assignment_output():
    # df["x"] = ... is exactly what the AST fallback rewrites to with_columns.
    return (
        "import polars as pl\n\n\n"
        "def load(df):\n"
        "    df[\"x\"] = df[\"a\"]\n"
        "    return df\n"
    )


def _run_single_py_step(agent, tmp_path):
    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    logs_dir = tmp_path / "logs"
    source_dir.mkdir(parents=True)
    (source_dir / "p.py").write_text('import pandas as pd\n\n\ndef load(df):\n    return df\n', encoding="utf-8")
    agent.run_step(
        project_dir,
        {
            "step_id": "step_001",
            "file": "src/p.py",
            "allowed_files": ["src/p.py"],
            "source_library": "pandas",
            "target_library": "polars",
        },
        logs_dir,
    )
    log = json.loads((logs_dir / "step_001_migration.json").read_text(encoding="utf-8"))
    code = (source_dir / "p.py").read_text(encoding="utf-8")
    return log, code


def test_research_mode_is_single_pass_raw(tmp_path):
    raw = _raw_pandas_assignment_output()
    agent = MigrationAgent.__new__(MigrationAgent)
    agent._chain = FakeMigrationChain([raw])
    agent._config = MigrationConfig.research()

    log, code = _run_single_py_step(agent, tmp_path)

    # No deterministic layer touched the output: file on disk == raw LLM output,
    # the AST fallback did NOT rewrite df["x"] = ..., single LLM call.
    assert log["pipeline"]["raw_llm_code"] == raw
    assert log["pipeline"]["layers_active"] == []
    assert log["pipeline"]["changed_by_ast"] is False
    assert code == raw
    assert len(agent._chain.calls) == 1


def test_ast_fallback_layer_rewrites_when_enabled(tmp_path):
    # Isolate the AST toggle: same raw input as the research test, only the AST
    # layer on. df["x"] = ... is rewritten to with_columns, proving the toggle.
    raw = _raw_pandas_assignment_output()
    agent = MigrationAgent.__new__(MigrationAgent)
    agent._chain = FakeMigrationChain([raw])
    agent._config = MigrationConfig(
        use_pattern_scanner=False,
        use_rescan_retry=False,
        use_ast_fallback=True,
        regenerate_invalid_syntax=False,
        enforce_symbol_scope=False,
    )

    log, code = _run_single_py_step(agent, tmp_path)

    assert log["pipeline"]["raw_llm_code"] == raw
    assert log["pipeline"]["changed_by_ast"] is True
    assert "ast" in log["pipeline"]["layers_active"]
    assert "with_columns" in code
    assert 'df["x"] =' not in code


def test_migration_does_not_run_pre_pytest_review_loop(tmp_path):
    # After the judge fusion, migration is a single pass: scanner -> LLM -> scope
    # -> AST. No implementation-review revision loop runs before validation, so a
    # valid first migration is written with exactly one migration-chain call and no
    # review dependency on the agent.
    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    logs_dir = tmp_path / "logs"
    source_dir.mkdir(parents=True)
    python_file = source_dir / "processing.py"
    python_file.write_text(
        "import pandas as pd\n\n\n"
        "def load(path):\n"
        "    return pd.read_csv(path)\n",
        encoding="utf-8",
    )
    agent = MigrationAgent.__new__(MigrationAgent)
    agent._chain = FakeMigrationChain(
        [
            "import polars as pl\n\n\n"
            "def load(path):\n"
            "    return pl.read_csv(path)\n",
        ]
    )

    result = agent.run_step(
        project_dir,
        {
            "step_id": "step_001",
            "file": "src/processing.py",
            "allowed_files": ["src/processing.py"],
            "source_library": "pandas",
            "target_library": "polars",
            "dataframe_flow_analysis": {"symbols": [], "groups": [], "notes": []},
        },
        logs_dir,
    )

    assert result["status"] == "completed"
    assert len(agent._chain.calls) == 1
    assert not hasattr(agent, "_implementation_review_agent")
    assert python_file.read_text(encoding="utf-8") == (
        "import polars as pl\n\n\n"
        "def load(path):\n"
        "    return pl.read_csv(path)\n"
    )


def test_implementation_review_with_issues_cannot_be_approved():
    agent = ImplementationReviewAgent.__new__(ImplementationReviewAgent)

    payload = agent._normalize_review_payload(
        {
            "agent": "implementation_review_agent",
            "step_id": "step_001",
            "file": "src/example.py",
            "status": "approved",
            "issues": [
                {
                    "kind": "polars_assignment_by_index",
                    "file": "src/example.py",
                    "symbol": "load",
                    "explanation": "Polars DataFrame uses pandas assignment syntax.",
                }
            ],
            "revision_instructions": "",
            "confidence": "high",
        }
    )

    assert payload["status"] == "needs_revision"
    assert "address every issue" in payload["revision_instructions"]


def test_implementation_review_rejects_removed_public_symbols():
    agent = ImplementationReviewAgent.__new__(ImplementationReviewAgent)

    payload = agent._normalize_review_payload(
        {
            "agent": "implementation_review_agent",
            "step_id": "step_001",
            "file": "src/example.py",
            "status": "approved",
            "issues": [],
            "revision_instructions": "",
            "confidence": "high",
        },
        rel_file="src/example.py",
        original_code=(
            "def load(path):\n"
            "    pass\n\n"
            "def invalid_rows(path):\n"
            "    pass\n"
        ),
        migrated_code=(
            "def load(path):\n"
            "    pass\n"
        ),
    )

    assert payload["status"] == "needs_revision"
    assert payload["issues"][0]["kind"] == "public_api_symbol_removed"
    assert payload["issues"][0]["symbol"] == "invalid_rows"
    assert "Missing symbols: invalid_rows" in payload["revision_instructions"]


def test_implementation_review_retries_missing_structured_output(tmp_path):
    agent = ImplementationReviewAgent.__new__(ImplementationReviewAgent)
    agent._chain = FakeReviewChain(
        [
            None,
            SimpleNamespace(
                model_dump=lambda: {
                    "status": "approved",
                    "issues": [],
                    "revision_instructions": "",
                    "confidence": "high",
                }
            ),
        ]
    )

    payload = agent.review(
        rel_file=tmp_path / "src" / "example.py",
        original_code="import pandas as pd\n",
        migrated_code="import polars as pl\n",
        planned_step={"step_id": "step_001"},
        dataframe_flow_analysis={"symbols": [], "groups": [], "notes": []},
        logs_dir=tmp_path / "logs",
    )

    assert payload["status"] == "approved"
    assert payload["structured_output_attempts"] == 2
    assert len(agent._chain.calls) == 2


def test_implementation_review_falls_back_when_structured_output_missing(tmp_path):
    agent = ImplementationReviewAgent.__new__(ImplementationReviewAgent)
    agent._chain = FakeReviewChain([None, None])

    payload = agent.review(
        rel_file=tmp_path / "src" / "example.py",
        original_code="import pandas as pd\n",
        migrated_code="import polars as pl\n",
        planned_step={"step_id": "step_001"},
        dataframe_flow_analysis={"symbols": [], "groups": [], "notes": []},
        logs_dir=tmp_path / "logs",
    )

    assert payload["status"] == "needs_revision"
    assert payload["confidence"] == "low"
    assert payload["structured_output_attempts"] == 2
    assert payload["issues"][0]["kind"] == "structured_output_missing"
    assert "did not return structured output" in payload["revision_instructions"]
    assert (tmp_path / "logs" / "step_001_implementation_review.json").exists()


def test_repair_agent_falls_back_when_structured_output_missing(tmp_path):
    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    logs_dir = tmp_path / "logs"
    source_dir.mkdir(parents=True)
    (source_dir / "processing.py").write_text("import polars as pl\n", encoding="utf-8")

    agent = RepairAgent.__new__(RepairAgent)
    agent._chain = FakeReviewChain([None])

    payload = agent.build_repair_plan(
        project_dir=project_dir,
        planned_step={"step_id": "step_001", "file": "src/processing.py"},
        migration_result={"changed": True},
        validation_evidence={
            "actionable_feedback": "Preserve semantic ordering.",
            "pytest_feedback": "E       AssertionError: row order differs",
        },
        logs_dir=logs_dir,
        attempt=1,
    )

    assert payload["failure_category"] == "unknown"
    assert payload["repair_strategy"] == "fallback_to_validation_feedback"
    assert "Preserve semantic ordering." in payload["instructions_for_migration_agent"]
    assert "Do not introduce benchmark-specific hardcoded values." in payload["must_not_do"]
    assert (logs_dir / "step_001_repair_01.json").exists()
