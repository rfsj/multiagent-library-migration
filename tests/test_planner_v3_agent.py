import json

import pytest

from src.agents.planner_v3_agent import (
    PlannerV3Agent,
    PlannerV3FileAnalysis,
    PlannerV3Plan,
    PlannerV3Step,
    PlannerV3SymbolAnalysis,
    PlannerV3SymbolAnalysisResult,
)


class FakeChain:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def invoke(self, payload):
        self.calls.append(payload)
        return self.result


def _agent(monkeypatch, *, plan, symbol_analysis=None, use_ast=True):
    def fake_init(self):
        self._chain = FakeChain(plan)
        self._symbol_analysis_chain = FakeChain(
            symbol_analysis or PlannerV3SymbolAnalysisResult()
        )
        self._use_ast = use_ast

    monkeypatch.setattr(PlannerV3Agent, "__init__", fake_init)
    return PlannerV3Agent()


def _write_project(tmp_path, files):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "requirements.txt").write_text(
        "pandas==2.2.3\npytest==8.3.4\n",
        encoding="utf-8",
    )
    for rel_path, source in files.items():
        path = project_dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return project_dir


def test_planner_v3_run_applies_least_scope_and_writes_logs(tmp_path, monkeypatch):
    project_dir = _write_project(
        tmp_path,
        {
            "src/orders.py": (
                "import pandas as pd\n\n\n"
                "def load_paid(path):\n"
                "    return pd.read_csv(path)\n\n\n"
                "def load_pending(path):\n"
                "    return pd.read_csv(path)\n"
            ),
            "tests/test_orders.py": "def test_placeholder():\n    assert True\n",
        },
    )
    logs_dir = tmp_path / "logs"
    plan = PlannerV3Plan(
        source_library="pandas",
        target_library="polars",
        dependency_files=["requirements.txt"],
        affected_files=["src/orders.py"],
        related_tests=["tests/test_orders.py"],
        complexity={"src/orders.py": "low"},
        migration_steps=[
            PlannerV3Step(
                step_id="step_001",
                file="src/orders.py",
                description="Migrate orders.",
                allowed_files=["src/orders.py"],
            )
        ],
    )
    symbol_analysis = PlannerV3SymbolAnalysisResult(
        files=[
            PlannerV3FileAnalysis(
                file="src/orders.py",
                symbols=[
                    PlannerV3SymbolAnalysis(
                        name="load_paid",
                        kind="function",
                        explicit_source_usage=True,
                        dataframe_like_usage=True,
                        confidence="high",
                    ),
                    PlannerV3SymbolAnalysis(
                        name="load_pending",
                        kind="function",
                        explicit_source_usage=True,
                        dataframe_like_usage=True,
                        confidence="high",
                    ),
                ],
            )
        ]
    )

    result = _agent(
        monkeypatch,
        plan=plan,
        symbol_analysis=symbol_analysis,
        use_ast=True,
    ).run(project_dir, logs_dir, "pandas", "polars")

    assert result["diagnosis_use_ast"] is True
    assert result["affected_source_files"] == ["src/orders.py"]
    assert [step["allowed_symbols"] for step in result["migration_steps"]] == [
        ["load_paid"],
        ["load_pending"],
    ]
    assert "requirements.txt" in result["migration_steps"][0]["allowed_files"]
    assert (logs_dir / "diagnosis_plan.json").exists()
    assert (logs_dir / "planner_symbol_analysis.json").exists()
    assert (logs_dir / "planner_guardrails.json").exists()
    persisted = json.loads((logs_dir / "diagnosis_plan.json").read_text())
    assert persisted["migration_steps"] == result["migration_steps"]


def test_planner_v3_ast_disabled_lets_llm_select_candidate_files(
    tmp_path, monkeypatch
):
    project_dir = _write_project(
        tmp_path,
        {
            "src/a.py": "def helper():\n    return 1\n",
            "src/b.py": "import pandas as pd\n\ndef load(path):\n    return pd.read_csv(path)\n",
            "tests/test_b.py": "def test_placeholder():\n    assert True\n",
        },
    )
    plan = PlannerV3Plan(
        source_library="pandas",
        target_library="polars",
        dependency_files=["requirements.txt"],
        affected_files=["src/b.py"],
        related_tests=[],
        complexity={"src/b.py": "low"},
        migration_steps=[
            PlannerV3Step(
                step_id="step_001",
                file="src/b.py",
                description="Migrate selected file.",
                allowed_files=["src/b.py"],
                allowed_symbols=["load"],
            )
        ],
    )
    agent = _agent(monkeypatch, plan=plan, use_ast=False)

    result = agent.run(project_dir, tmp_path / "logs", "pandas", "polars")

    assert result["diagnosis_use_ast"] is False
    assert result["affected_source_files"] == ["src/b.py"]
    assert agent._chain.calls[0]["affected_files"] == ["src/a.py", "src/b.py"]
    assert result["migration_steps"][0]["allowed_symbols"] == ["load"]
    assert any(
        "DIAGNOSIS_USE_AST=0" in warning for warning in result["planner_warnings"]
    )


def test_planner_v3_guardrails_remove_tests_and_unsafe_scope(tmp_path, monkeypatch):
    project_dir = _write_project(
        tmp_path,
        {
            "src/orders.py": "import pandas as pd\n\ndef load(path):\n    return pd.read_csv(path)\n",
            "tests/test_orders.py": "import pandas as pd\n",
        },
    )
    plan = PlannerV3Plan(
        source_library="pandas",
        target_library="polars",
        dependency_files=["requirements.txt"],
        affected_files=["src/orders.py"],
        related_tests=["tests/test_orders.py"],
        complexity={"src/orders.py": "low"},
        migration_steps=[
            PlannerV3Step(
                step_id="step_001",
                file="tests/test_orders.py",
                description="Invalid test target.",
                allowed_files=["tests/test_orders.py"],
            ),
            PlannerV3Step(
                step_id="step_002",
                file="src/orders.py",
                description="Migrate orders.",
                allowed_files=["src/orders.py", "tests/test_orders.py", "../bad.py"],
                allowed_symbols=["missing_symbol"],
            ),
        ],
    )

    result = _agent(monkeypatch, plan=plan, use_ast=True).run(
        project_dir, tmp_path / "logs", "pandas", "polars"
    )

    assert len(result["migration_steps"]) == 1
    step = result["migration_steps"][0]
    assert step["file"] == "src/orders.py"
    assert "tests/test_orders.py" not in step["allowed_files"]
    assert "../bad.py" not in step["allowed_files"]
    assert step["allowed_symbols"] == []
    guardrails = json.loads(
        (tmp_path / "logs" / "planner_guardrails.json").read_text()
    )
    assert {event["rule"] for event in guardrails} >= {
        "test_file_target",
        "allowed_files_scope",
        "valid_allowed_symbols",
    }


def test_planner_v3_groups_cross_file_dataframe_flow(tmp_path, monkeypatch):
    project_dir = _write_project(
        tmp_path,
        {
            "src/loaders.py": "import pandas as pd\n\ndef load_orders(path):\n    return pd.read_csv(path)\n",
            "src/reports.py": (
                "import pandas as pd\n"
                "from src.loaders import load_orders\n\n"
                "def summarize(path):\n"
                "    return load_orders(path).groupby('region').sum()\n"
            ),
        },
    )
    plan = PlannerV3Plan(
        source_library="pandas",
        target_library="polars",
        dependency_files=["requirements.txt"],
        affected_files=["src/loaders.py", "src/reports.py"],
        related_tests=[],
        complexity={"src/loaders.py": "low", "src/reports.py": "medium"},
        migration_steps=[
            PlannerV3Step(
                step_id="step_001",
                file="src/loaders.py",
                description="Migrate loaders.",
                allowed_files=["src/loaders.py"],
            ),
            PlannerV3Step(
                step_id="step_002",
                file="src/reports.py",
                description="Migrate reports.",
                allowed_files=["src/reports.py"],
            ),
        ],
    )
    symbol_analysis = PlannerV3SymbolAnalysisResult(
        files=[
            PlannerV3FileAnalysis(
                file="src/loaders.py",
                symbols=[
                    PlannerV3SymbolAnalysis(
                        name="load_orders",
                        kind="function",
                        explicit_source_usage=True,
                        creates_dataframe_like=True,
                        returns_dataframe_like=True,
                        confidence="high",
                    )
                ],
            ),
            PlannerV3FileAnalysis(
                file="src/reports.py",
                symbols=[
                    PlannerV3SymbolAnalysis(
                        name="summarize",
                        kind="function",
                        dataframe_like_usage=True,
                        receives_dataframe_like=True,
                        consumes_dataframe_from=["load_orders"],
                        confidence="high",
                    )
                ],
            ),
        ]
    )

    result = _agent(
        monkeypatch,
        plan=plan,
        symbol_analysis=symbol_analysis,
        use_ast=True,
    ).run(project_dir, tmp_path / "logs", "pandas", "polars")

    assert len(result["migration_steps"]) == 1
    step = result["migration_steps"][0]
    assert step["step_type"] == "grouped"
    assert step["files"] == ["src/loaders.py", "src/reports.py"]
    assert step["allowed_symbols"] == []
    assert result["dataframe_flow_analysis"]["groups"][0]["planning_strategy"] == (
        "grouped_before_consumers"
    )
