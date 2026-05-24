from __future__ import annotations

import os

from langchain_core.language_models import BaseChatModel


def get_llm() -> BaseChatModel:
    """Instantiate the configured LangChain chat model from env vars.

    Required env vars:
      LLM_PROVIDER    — "anthropic" or "google"
      DIAGNOSIS_MODEL — model name for the chosen provider
    """
    provider = os.environ["LLM_PROVIDER"].lower()
    model = os.environ["DIAGNOSIS_MODEL"]

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic  # noqa: PLC0415

        return ChatAnthropic(model=model)

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI  # noqa: PLC0415

        return ChatGoogleGenerativeAI(model=model)

    raise ValueError(
        f"Unsupported LLM_PROVIDER '{provider}'. "
        "Valid values: 'anthropic', 'google'."
    )
