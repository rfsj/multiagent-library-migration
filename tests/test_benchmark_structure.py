from pathlib import Path

from src.tools.project_scanner import build_project_audit, scan_project
from src.agents.diagnosis_agent import (
    DiagnosisAgent,
    MigrationStep,
    _migratable_symbols,
    _should_keep_file_level_step,
)


def test_task_001_contains_expected_pandas_usage():
    project_dir = Path("benchmark/task_001_read_csv_filter/input_project")

    scan = scan_project(project_dir, "pandas")

    assert "requirements.txt" in scan["dependency_files"]
    assert "src/orders/processing.py" in scan["affected_files"]
    assert {call["api"] for call in scan["source_api_calls"]} >= {
        "pd.read_csv",
        "boolean_filter",
        "column_selection",
        "sort_values",
    }


def test_scanner_separates_source_and_test_pandas_usage(tmp_path):
    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    tests_dir = project_dir / "tests"
    source_dir.mkdir(parents=True)
    tests_dir.mkdir()
    (project_dir / "requirements.txt").write_text("pandas==2.2.3\n", encoding="utf-8")
    (source_dir / "processing.py").write_text(
        "import pandas as pd\n\n"
        "def load(path):\n"
        "    return pd.read_csv(path)\n",
        encoding="utf-8",
    )
    (tests_dir / "test_processing.py").write_text(
        "import pandas as pd\n\n"
        "def test_builds_expected_frame():\n"
        "    assert len(pd.DataFrame({'a': [1]})) == 1\n",
        encoding="utf-8",
    )

    audit = build_project_audit(project_dir, "pandas", "polars")

    assert audit["affected_source_files"] == ["src/processing.py"]
    assert audit["test_files_with_source_library_usage"] == ["tests/test_processing.py"]
    assert audit["source_import_count"] == 1
    assert audit["test_import_count"] == 1
    assert audit["dependency_summary"]["source_dependency_present"] is True
    assert audit["dependency_summary"]["target_dependency_action"] == "add_dependency"


def test_diagnosis_symbol_detection_finds_dataframe_functions(tmp_path):
    source = tmp_path / "processing.py"
    source.write_text(
        "import pandas as pd\n\n\n"
        "def load(path):\n"
        "    return pd.read_csv(path)\n\n\n"
        "def summarize(df):\n"
        "    return df.groupby('region').agg({'total': 'sum'})\n\n\n"
        "def helper(value):\n"
        "    return value + 1\n",
        encoding="utf-8",
    )

    assert _migratable_symbols(source, "pandas") == ["load", "summarize"]


def test_diagnosis_keeps_coupled_analytics_module_as_file_level_step(tmp_path):
    source = tmp_path / "processing.py"
    source.write_text(
        "import pandas as pd\n\n\n"
        "def load_table(path):\n"
        "    return pd.read_csv(path)\n\n\n"
        "def summarize_by_region(path):\n"
        "    return load_table(path).groupby('region').sum()\n\n\n"
        "def latest_by_account(path):\n"
        "    return load_table(path).drop_duplicates(subset=['account_id'])\n",
        encoding="utf-8",
    )

    assert _should_keep_file_level_step(source, "pandas") is True


def test_diagnosis_discards_dataframe_methods_from_allowed_symbols(tmp_path):
    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "summaries.py").write_text(
        "import pandas as pd\n\n\n"
        "def revenue_by_region(path):\n"
        "    df = pd.read_csv(path)\n"
        "    return df.groupby('region').sum()\n\n\n"
        "def latest_order(path):\n"
        "    df = pd.read_csv(path)\n"
        "    return df.drop_duplicates(subset=['customer_id'])\n",
        encoding="utf-8",
    )

    steps, warnings = DiagnosisAgent.__new__(DiagnosisAgent)._sanitize_migration_steps(
        [
            MigrationStep(
                step_id="step_001",
                file="src/summaries.py",
                description="Migrate pandas methods.",
                allowed_files=["src/summaries.py"],
                allowed_symbols=["groupby", "drop_duplicates"],
            )
        ],
        ["src/summaries.py"],
        [],
        {"target_dependency_action": "none"},
        project_dir,
        "pandas",
    )

    assert [step["allowed_symbols"] for step in steps] == [
        ["revenue_by_region"],
        ["latest_order"],
    ]
    assert any("removed non-top-level symbols" in warning for warning in warnings)


def test_diagnosis_deduplicates_api_level_steps_before_symbol_split(tmp_path):
    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "analytics.py").write_text(
        "import pandas as pd\n\n\n"
        "def load_table(path):\n"
        "    return pd.read_csv(path)\n\n\n"
        "def summarize(path):\n"
        "    df = pd.read_csv(path)\n"
        "    return df.groupby('region').sum()\n\n\n"
        "def latest(path):\n"
        "    df = pd.read_csv(path)\n"
        "    return df.drop_duplicates(subset=['customer_id'])\n",
        encoding="utf-8",
    )

    steps, warnings = DiagnosisAgent.__new__(DiagnosisAgent)._sanitize_migration_steps(
        [
            MigrationStep(
                step_id="step_001",
                file="src/analytics.py",
                description="Migrate read_csv.",
                allowed_files=["src/analytics.py"],
                allowed_symbols=["pd.read_csv"],
            ),
            MigrationStep(
                step_id="step_002",
                file="src/analytics.py",
                description="Migrate groupby.",
                allowed_files=["src/analytics.py"],
                allowed_symbols=["pd.DataFrame.groupby"],
            ),
            MigrationStep(
                step_id="step_003",
                file="src/analytics.py",
                description="Migrate drop duplicates.",
                allowed_files=["src/analytics.py"],
                allowed_symbols=["pd.DataFrame.drop_duplicates"],
            ),
        ],
        ["src/analytics.py"],
        [],
        {"target_dependency_action": "none"},
        project_dir,
        "pandas",
    )

    assert [step["allowed_symbols"] for step in steps] == [
        ["load_table"],
        ["summarize"],
        ["latest"],
    ]
    assert any("Deduplicated 2 redundant migration step(s)" in warning for warning in warnings)


def test_diagnosis_flow_analysis_keeps_coupled_files_at_file_level(tmp_path):
    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "loaders.py").write_text(
        "import pandas as pd\n\n\n"
        "def load_orders(path):\n"
        "    return pd.read_csv(path)\n\n\n"
        "def paid_orders(path):\n"
        "    orders = load_orders(path)\n"
        "    return orders[orders['status'] == 'paid']\n",
        encoding="utf-8",
    )
    (source_dir / "summaries.py").write_text(
        "from src.loaders import paid_orders\n\n\n"
        "def revenue_by_region(path):\n"
        "    paid = paid_orders(path)\n"
        "    return paid.groupby('region').sum()\n\n\n"
        "def monthly_product_matrix(path):\n"
        "    paid = paid_orders(path)\n"
        "    return paid.pivot_table(index='region')\n",
        encoding="utf-8",
    )

    steps, warnings = DiagnosisAgent.__new__(DiagnosisAgent)._sanitize_migration_steps(
        [
            MigrationStep(
                step_id="step_001",
                file="src/loaders.py",
                description="Migrate loaders.",
                allowed_files=["src/loaders.py"],
            ),
            MigrationStep(
                step_id="step_002",
                file="src/summaries.py",
                description="Migrate summaries.",
                allowed_files=["src/summaries.py"],
            ),
        ],
        ["src/loaders.py", "src/summaries.py"],
        [],
        {"target_dependency_action": "none"},
        project_dir,
        "pandas",
        {
            "groups": [
                {
                    "group_id": "flow_group_001",
                    "files": ["src/loaders.py", "src/summaries.py"],
                    "symbols": ["paid_orders", "revenue_by_region"],
                    "reason": "summaries consumes DataFrames returned by loaders",
                    "planning_strategy": "file_level_steps",
                }
            ]
        },
    )

    assert [step["file"] for step in steps] == ["src/loaders.py", "src/summaries.py"]
    assert [step["allowed_symbols"] for step in steps] == [[], []]
    assert any("DataFrame flow analysis marked it as coupled" in warning for warning in warnings)


def test_diagnosis_groups_cross_file_dataframe_flow_when_required(tmp_path):
    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "loaders.py").write_text(
        "import pandas as pd\n\n\n"
        "def load_orders(path):\n"
        "    return pd.read_csv(path)\n",
        encoding="utf-8",
    )
    (source_dir / "summaries.py").write_text(
        "from src.loaders import load_orders\n\n\n"
        "def revenue_by_region(path):\n"
        "    orders = load_orders(path)\n"
        "    return orders.groupby('region').sum()\n",
        encoding="utf-8",
    )

    steps, warnings = DiagnosisAgent.__new__(DiagnosisAgent)._sanitize_migration_steps(
        [
            MigrationStep(
                step_id="step_001",
                file="src/loaders.py",
                description="Migrate loaders.",
                allowed_files=["src/loaders.py"],
            ),
            MigrationStep(
                step_id="step_002",
                file="src/summaries.py",
                description="Migrate summaries.",
                allowed_files=["src/summaries.py"],
            ),
        ],
        ["src/loaders.py", "src/summaries.py"],
        [],
        {"target_dependency_action": "none"},
        project_dir,
        "pandas",
        {
            "groups": [
                {
                    "group_id": "flow_group_001",
                    "files": ["src/loaders.py", "src/summaries.py"],
                    "symbols": ["load_orders", "revenue_by_region"],
                    "reason": "consumer requires producer type migration first",
                    "planning_strategy": "grouped_before_consumers",
                }
            ]
        },
    )

    assert len(steps) == 1
    assert steps[0]["file"] == "src/loaders.py"
    assert steps[0]["files"] == ["src/loaders.py", "src/summaries.py"]
    assert steps[0]["allowed_files"] == ["src/loaders.py", "src/summaries.py"]
    assert steps[0]["allowed_symbols"] == []
    assert any("Grouped DataFrame flow files" in warning for warning in warnings)


def test_diagnosis_flow_symbol_dependencies_keep_cross_file_consumers_at_file_level(tmp_path):
    project_dir = tmp_path / "project"
    source_dir = project_dir / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "loaders.py").write_text(
        "import pandas as pd\n\n\n"
        "def paid_orders(path):\n"
        "    return pd.read_csv(path)\n",
        encoding="utf-8",
    )
    (source_dir / "summaries.py").write_text(
        "from src.loaders import paid_orders\n\n\n"
        "def revenue_by_region(path):\n"
        "    paid = paid_orders(path)\n"
        "    return paid.groupby('region').sum()\n\n\n"
        "def monthly_product_matrix(path):\n"
        "    paid = paid_orders(path)\n"
        "    return paid.pivot_table(index='region')\n",
        encoding="utf-8",
    )

    steps, warnings = DiagnosisAgent.__new__(DiagnosisAgent)._sanitize_migration_steps(
        [
            MigrationStep(
                step_id="step_001",
                file="src/loaders.py",
                description="Migrate loaders.",
                allowed_files=["src/loaders.py"],
            ),
            MigrationStep(
                step_id="step_002",
                file="src/summaries.py",
                description="Migrate summaries.",
                allowed_files=["src/summaries.py"],
            ),
        ],
        ["src/loaders.py", "src/summaries.py"],
        [],
        {"target_dependency_action": "none"},
        project_dir,
        "pandas",
        {
            "symbols": [
                {
                    "file": "src/loaders.py",
                    "symbol": "paid_orders",
                    "role": "producer",
                    "returns_dataframe": True,
                    "consumes_dataframe_from": [],
                    "type_contract": "pandas.DataFrame",
                },
                {
                    "file": "src/summaries.py",
                    "symbol": "revenue_by_region",
                    "role": "consumer",
                    "returns_dataframe": True,
                    "consumes_dataframe_from": ["paid_orders"],
                    "type_contract": "pandas.DataFrame",
                },
            ],
            "groups": [
                {
                    "group_id": "flow_group_001",
                    "files": ["src/loaders.py"],
                    "symbols": ["paid_orders"],
                    "reason": "producer file",
                    "planning_strategy": "file_level_steps",
                },
                {
                    "group_id": "flow_group_002",
                    "files": ["src/summaries.py"],
                    "symbols": ["revenue_by_region"],
                    "reason": "consumer file",
                    "planning_strategy": "file_level_steps",
                },
            ],
        },
    )

    assert [step["file"] for step in steps] == ["src/loaders.py", "src/summaries.py"]
    assert [step["allowed_symbols"] for step in steps] == [[], []]
    assert any("DataFrame flow analysis marked it as coupled" in warning for warning in warnings)
