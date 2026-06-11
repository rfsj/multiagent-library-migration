"""Configuration of the migration pipeline's deterministic assistance layers.

This framework exists to *study the LLM's behavior*, so the default is a pure,
single-pass LLM migration with no deterministic assistance contaminating the
signal. Each assistance layer (prompt scanner, rescan retry, AST fallback) and
each correctness guard (syntax regen, symbol scope) is an opt-in experimental
condition that can be ablated independently. See ai_docs/proposal-research-mode.md.

Mode is chosen by MIGRATION_MODE (research | assisted, default research). Any
individual layer can be overridden with MIGRATION_USE_* env vars regardless of
the preset.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_flag(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class MigrationConfig:
    use_pattern_scanner: bool      # inject known-gotcha hints into the prompt
    use_rescan_retry: bool         # re-scan output and force a retry on remaining patterns
    use_ast_fallback: bool         # deterministically rewrite mechanical patterns
    regenerate_invalid_syntax: bool  # guard: re-ask the LLM if output is not valid Python
    enforce_symbol_scope: bool     # guard: clip output back to allowed symbols

    @property
    def mode(self) -> str:
        if self == self.assisted():
            return "assisted"
        if self == self.research():
            return "research"
        return "custom"

    @classmethod
    def research(cls) -> "MigrationConfig":
        """Pure single-pass LLM: no assistance, no guards. Raw is raw."""
        return cls(
            use_pattern_scanner=False,
            use_rescan_retry=False,
            use_ast_fallback=False,
            regenerate_invalid_syntax=False,
            enforce_symbol_scope=False,
        )

    @classmethod
    def assisted(cls) -> "MigrationConfig":
        """Product behavior: every assistance layer and guard on."""
        return cls(
            use_pattern_scanner=True,
            use_rescan_retry=True,
            use_ast_fallback=True,
            regenerate_invalid_syntax=True,
            enforce_symbol_scope=True,
        )

    @classmethod
    def from_env(cls) -> "MigrationConfig":
        mode = os.environ.get("MIGRATION_MODE", "research").strip().lower()
        base = cls.assisted() if mode == "assisted" else cls.research()
        return cls(
            use_pattern_scanner=_pick(_env_flag("MIGRATION_USE_SCANNER"), base.use_pattern_scanner),
            use_rescan_retry=_pick(_env_flag("MIGRATION_USE_RESCAN"), base.use_rescan_retry),
            use_ast_fallback=_pick(_env_flag("MIGRATION_USE_AST"), base.use_ast_fallback),
            regenerate_invalid_syntax=_pick(
                _env_flag("MIGRATION_USE_SYNTAX_REGEN"), base.regenerate_invalid_syntax
            ),
            enforce_symbol_scope=_pick(_env_flag("MIGRATION_USE_SCOPE"), base.enforce_symbol_scope),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "use_pattern_scanner": self.use_pattern_scanner,
            "use_rescan_retry": self.use_rescan_retry,
            "use_ast_fallback": self.use_ast_fallback,
            "regenerate_invalid_syntax": self.regenerate_invalid_syntax,
            "enforce_symbol_scope": self.enforce_symbol_scope,
        }


def _pick(override: bool | None, base: bool) -> bool:
    return base if override is None else override
