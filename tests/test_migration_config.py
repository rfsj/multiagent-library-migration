import pytest

from src.migration_config import MigrationConfig

_ENV_VARS = [
    "MIGRATION_MODE",
    "MIGRATION_USE_SCANNER",
    "MIGRATION_USE_RESCAN",
    "MIGRATION_USE_AST",
    "MIGRATION_USE_SYNTAX_REGEN",
    "MIGRATION_USE_SCOPE",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_research_preset_disables_everything():
    cfg = MigrationConfig.research()
    assert not any(
        [
            cfg.use_pattern_scanner,
            cfg.use_rescan_retry,
            cfg.use_ast_fallback,
            cfg.regenerate_invalid_syntax,
            cfg.enforce_symbol_scope,
        ]
    )
    assert cfg.mode == "research"


def test_assisted_preset_enables_everything():
    cfg = MigrationConfig.assisted()
    assert all(
        [
            cfg.use_pattern_scanner,
            cfg.use_rescan_retry,
            cfg.use_ast_fallback,
            cfg.regenerate_invalid_syntax,
            cfg.enforce_symbol_scope,
        ]
    )
    assert cfg.mode == "assisted"


def test_default_mode_is_research():
    assert MigrationConfig.from_env() == MigrationConfig.research()


def test_mode_env_selects_assisted(monkeypatch):
    monkeypatch.setenv("MIGRATION_MODE", "assisted")
    assert MigrationConfig.from_env() == MigrationConfig.assisted()


def test_per_layer_override_on_top_of_research(monkeypatch):
    monkeypatch.setenv("MIGRATION_USE_SCANNER", "1")
    cfg = MigrationConfig.from_env()
    assert cfg.use_pattern_scanner is True
    # everything else stays off (research baseline)
    assert cfg.use_ast_fallback is False
    assert cfg.use_rescan_retry is False
    assert cfg.mode == "custom"


def test_ast_override_works_in_research_mode(monkeypatch):
    # The single AST switch: MIGRATION_USE_AST turns the layer on in any mode.
    monkeypatch.setenv("MIGRATION_USE_AST", "1")
    cfg = MigrationConfig.from_env()
    assert cfg.use_ast_fallback is True
    assert cfg.use_pattern_scanner is False  # rest stays research
    assert cfg.mode == "custom"


def test_ast_override_can_disable_in_assisted_mode(monkeypatch):
    monkeypatch.setenv("MIGRATION_MODE", "assisted")
    monkeypatch.setenv("MIGRATION_USE_AST", "0")
    cfg = MigrationConfig.from_env()
    assert cfg.use_ast_fallback is False
    assert cfg.use_pattern_scanner is True  # other assisted layers stay on


def test_as_dict_roundtrips_mode_and_flags():
    d = MigrationConfig.research().as_dict()
    assert d["mode"] == "research"
    assert d["use_ast_fallback"] is False
