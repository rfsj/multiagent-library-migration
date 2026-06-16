from __future__ import annotations

import os
from collections.abc import Iterator

from langchain_core.language_models import BaseChatModel

_TIMEOUT_TYPE_NAMES = frozenset(
    {
        "APITimeoutError",
        "DeadlineExceeded",
        "TimeoutException",
        "ReadTimeout",
        "WriteTimeout",
        "ConnectTimeout",
        "PoolTimeout",
    }
)
_TIMEOUT_MESSAGE_FRAGMENTS = (
    "timed out",
    "timeout",
    "deadline exceeded",
)


def get_llm() -> BaseChatModel:
    """Instantiate the configured LangChain chat model from env vars.

    Required env vars:
      LLM_PROVIDER — "anthropic" or "google"
      LLM_MODEL    — model name for the chosen provider
    """
    provider = os.environ["LLM_PROVIDER"].lower()
    model = os.environ["LLM_MODEL"]
    timeout = get_llm_request_timeout_seconds()

    from src import llm_proxy  # noqa: PLC0415

    cb = llm_proxy.get_callback()
    callbacks = [cb] if cb else None

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic  # noqa: PLC0415

        kwargs = {"model": model, "callbacks": callbacks}
        if timeout is not None:
            kwargs["timeout"] = timeout
        return ChatAnthropic(**kwargs)

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI  # noqa: PLC0415

        kwargs = {"model": model, "callbacks": callbacks}
        if timeout is not None:
            kwargs["request_timeout"] = timeout
        return ChatGoogleGenerativeAI(**kwargs)

    raise ValueError(
        f"Unsupported LLM_PROVIDER '{provider}'. Valid values: 'anthropic', 'google'."
    )


def get_llm_request_timeout_seconds() -> float | None:
    raw = os.environ.get("LLM_REQUEST_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return None
    timeout = float(raw)
    return timeout if timeout > 0 else None


def is_llm_timeout_error(exc: BaseException) -> bool:
    return any(_is_timeout_exception(error) for error in _exception_chain(exc))


def format_llm_timeout_error(context: str, exc: BaseException) -> str:
    return f"LLM timeout during {context}: {exc}"


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _is_timeout_exception(exc: BaseException) -> bool:
    if exc.__class__.__name__ in _TIMEOUT_TYPE_NAMES:
        return True
    message = str(exc).lower()
    return any(fragment in message for fragment in _TIMEOUT_MESSAGE_FRAGMENTS)
