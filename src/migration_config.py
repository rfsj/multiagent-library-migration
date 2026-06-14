"""Configuration of the migration pipeline's deterministic assistance layers.

This framework exists to *study the LLM's behavior*, so the default is a pure,
single-pass LLM migration with no deterministic assistance contaminating the
signal. Each assistance layer (prompt scanner, rescan retry, AST fallback) and
each correctness guard (syntax regen, symbol scope) is an opt-in experimental
condition that can be ablated independently. See ai_docs/proposal-research-mode.md.

Mode is chosen by MIGRATION_MODE. Besides the two presets `research` (pure,
single-pass LLM) and `assisted` (every layer on), there are four research-mode
variants that ablate the two prompt-side techniques on top of the pure base:

    research                -> pure LLM (no CoT, no few-shot)        [a.k.a. "puro"]
    research_cot            -> CoT only
    research_fewshot        -> few-shot only
    research_cot_fewshot    -> CoT + few-shot                        [a.k.a. "research_both"]

CoT (`use_cot`) and few-shot (`use_few_shot`) are *prompt-side* research knobs;
the other layers (scanner/rescan/AST/syntax/scope) are the deterministic
assistance. Any individual layer can still be overridden with MIGRATION_USE_*
env vars regardless of the preset.

Note: a clean CoT ablation requires a CoT-free base prompt (e.g.
migration_agent_v4.md). With v5 as base the instruction is already inline, so the
`use_cot=False` modes would not be truly CoT-free. See MIGRATION_PROMPT_FILE.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace


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
    use_few_shot: bool = False     # prepend fixed input/output example pairs to the prompt
    use_cot: bool = False          # inject a chain-of-thought instruction (fill migration_plan first)

    @property
    def mode(self) -> str:
        if self == self.assisted():
            return "assisted"
        pure_base = not any((
            self.use_pattern_scanner,
            self.use_rescan_retry,
            self.use_ast_fallback,
            self.regenerate_invalid_syntax,
            self.enforce_symbol_scope,
        ))
        if pure_base:
            return {
                (False, False): "research",
                (True, False): "research_cot",
                (False, True): "research_fewshot",
                (True, True): "research_cot_fewshot",
            }[(self.use_cot, self.use_few_shot)]
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
            use_few_shot=False,
            use_cot=False,
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
            use_few_shot=True,
            use_cot=True,
        )

    @classmethod
    def from_env(cls) -> "MigrationConfig":
        mode = os.environ.get("MIGRATION_MODE", "research").strip().lower().replace("-", "_")
        base = _base_for_mode(cls, mode)
        return cls(
            use_pattern_scanner=_pick(_env_flag("MIGRATION_USE_SCANNER"), base.use_pattern_scanner),
            use_rescan_retry=_pick(_env_flag("MIGRATION_USE_RESCAN"), base.use_rescan_retry),
            use_ast_fallback=_pick(_env_flag("MIGRATION_USE_AST"), base.use_ast_fallback),
            regenerate_invalid_syntax=_pick(
                _env_flag("MIGRATION_USE_SYNTAX_REGEN"), base.regenerate_invalid_syntax
            ),
            enforce_symbol_scope=_pick(_env_flag("MIGRATION_USE_SCOPE"), base.enforce_symbol_scope),
            use_few_shot=_pick(_env_flag("MIGRATION_USE_FEWSHOT"), base.use_few_shot),
            use_cot=_pick(_env_flag("MIGRATION_USE_COT"), base.use_cot),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "use_pattern_scanner": self.use_pattern_scanner,
            "use_rescan_retry": self.use_rescan_retry,
            "use_ast_fallback": self.use_ast_fallback,
            "regenerate_invalid_syntax": self.regenerate_invalid_syntax,
            "enforce_symbol_scope": self.enforce_symbol_scope,
            "use_few_shot": self.use_few_shot,
            "use_cot": self.use_cot,
        }


def _base_for_mode(cls: type["MigrationConfig"], mode: str) -> "MigrationConfig":
    """Resolve the preset for a MIGRATION_MODE value (before per-layer overrides)."""
    if mode == "assisted":
        return cls.assisted()
    research = cls.research()
    if mode in ("research_cot", "cot"):
        return replace(research, use_cot=True)
    if mode in ("research_fewshot", "research_few_shot", "fewshot", "few_shot"):
        return replace(research, use_few_shot=True)
    if mode in ("research_cot_fewshot", "research_both", "both"):
        return replace(research, use_cot=True, use_few_shot=True)
    # "research", "puro", "pure", or anything unrecognized -> pure base.
    return research


def _pick(override: bool | None, base: bool) -> bool:
    return base if override is None else override
