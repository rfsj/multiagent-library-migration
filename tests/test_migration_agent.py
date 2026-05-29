import json

import pytest

from src.agents.migration_agent import MigrationAgent


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
