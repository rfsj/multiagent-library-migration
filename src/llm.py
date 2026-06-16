from __future__ import annotations

import os

from langchain_core.language_models import BaseChatModel


def get_llm() -> BaseChatModel:
    """Instantiate the configured LangChain chat model from env vars.

    Required env vars:
      LLM_PROVIDER — "anthropic" or "google"
      LLM_MODEL    — model name for the chosen provider
    """
    provider = os.environ["LLM_PROVIDER"].lower()
    model = os.environ["LLM_MODEL"]

    from src import llm_proxy  # noqa: PLC0415

    cb = llm_proxy.get_callback()
    callbacks = [cb] if cb else None

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic  # noqa: PLC0415

        return ChatAnthropic(model=model, callbacks=callbacks)

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI  # noqa: PLC0415

        return ChatGoogleGenerativeAI(model=model, callbacks=callbacks)

    raise ValueError(
        f"Unsupported LLM_PROVIDER '{provider}'. Valid values: 'anthropic', 'google'."
    )
