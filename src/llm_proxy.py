from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

_log_path: Path | None = None
_call_counts: dict[str, int] = {}
_current_label: str | None = None


def configure(log_path: Path) -> None:
    global _log_path
    _log_path = log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    reset_counts()


def set_label(label: str | None) -> None:
    """Tag subsequent LLM calls so they can be counted per workflow phase/step.

    The workflow runs single-threaded inside LangGraph's ``invoke``, so a module
    global is enough to attribute every nested LLM call (migration, regeneration,
    review, repair) to the step that triggered it.
    """
    global _current_label
    _current_label = label


def reset_counts() -> None:
    global _current_label
    _call_counts.clear()
    _current_label = None


def call_counts() -> dict[str, int]:
    return dict(_call_counts)


def total_calls() -> int:
    return sum(_call_counts.values())


def _record_call() -> None:
    label = _current_label or "unlabeled"
    _call_counts[label] = _call_counts.get(label, 0) + 1


def get_callback() -> LLMProxyLogger | None:
    if _log_path is None:
        return None
    return LLMProxyLogger(_log_path)


class LLMProxyLogger(BaseCallbackHandler):
    def __init__(self, log_path: Path) -> None:
        super().__init__()
        self._log_path = log_path

    def _write(self, entry: dict) -> None:
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        **kwargs: Any,
    ) -> None:
        kwargs_model = (kwargs.get("invocation_params") or {}).get("model") or (
            kwargs.get("invocation_params") or {}
        ).get("model_name")
        model_id = (
            kwargs_model
            or serialized.get("name")
            or (serialized.get("id") or ["unknown"])[-1]
        )
        _record_call()
        self._write(
            {
                "event": "request",
                "ts": datetime.now().isoformat(),
                "run_id": str(kwargs.get("run_id", "")),
                "label": _current_label or "unlabeled",
                "model": model_id,
                "messages": [
                    [{"role": m.type, "content": m.content} for m in batch]
                    for batch in messages
                ],
            }
        )

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        generations = []
        for batch in response.generations:
            batch_out = []
            for g in batch:
                entry: dict[str, Any] = {}
                if g.text:
                    entry["text"] = g.text
                if hasattr(g, "message"):
                    msg = g.message
                    if hasattr(msg, "content") and msg.content:
                        entry["content"] = msg.content
                    extra = getattr(msg, "additional_kwargs", {})
                    # Anthropic e OpenAI retornam structured output via tool_calls (OpenAI Tools API).
                    # Google Gemini retorna via function_call (formato legado da própria API do Gemini).
                    # Com with_structured_output(), o texto da resposta fica vazio nos dois casos —
                    # o payload real está nesses campos, daí capturar os dois.
                    if extra.get("tool_calls"):
                        entry["tool_calls"] = extra["tool_calls"]
                    if extra.get("function_call"):
                        entry["function_call"] = extra["function_call"]
                if hasattr(g, "generation_info") and g.generation_info:
                    entry["generation_info"] = g.generation_info
                batch_out.append(entry)
            generations.append(batch_out)

        self._write(
            {
                "event": "response",
                "ts": datetime.now().isoformat(),
                "run_id": str(kwargs.get("run_id", "")),
                "generations": generations,
                "usage": response.llm_output,
            }
        )
