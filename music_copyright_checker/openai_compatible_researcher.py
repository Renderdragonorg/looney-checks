"""Runs the licensing research step through a generic OpenAI-compatible endpoint.

Same contract as :class:`~music_copyright_checker.openrouter_researcher.OpenRouterResearcher`,
but for providers that do not implement OpenRouter's server-side web search
tool. Live research runs through the client-side Exa function tool.
"""

from __future__ import annotations

from typing import Optional

from .exa_search import DEFAULT_EXA_BASE_URL
from .openai_compatible_client import (
    DEFAULT_OPENAI_COMPAT_TIMEOUT,
    OpenAICompatibleClient,
)
from .openrouter_researcher import OpenRouterResearcher


class OpenAICompatibleResearcher(OpenRouterResearcher):
    """Wraps :class:`OpenAICompatibleClient` to run the licensing-research prompt."""

    provider_label = "OpenAI-compatible"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = DEFAULT_OPENAI_COMPAT_TIMEOUT,
        api_key_env: Optional[str] = None,
        web_search: bool = True,
        search_backend: Optional[str] = None,
        exa_api_key: Optional[str] = None,
        exa_base_url: Optional[str] = None,
        exa_timeout: Optional[float] = None,
        client: Optional[OpenAICompatibleClient] = None,
    ) -> None:
        if client is None:
            client = OpenAICompatibleClient(
                api_key=api_key,
                base_url=base_url,
                model=model,
                timeout=timeout,
                api_key_env=api_key_env,
                search_backend=search_backend,
                exa_api_key=exa_api_key,
                exa_base_url=exa_base_url or DEFAULT_EXA_BASE_URL,
                exa_timeout=exa_timeout,
            )
        super().__init__(
            model=model,
            timeout=timeout,
            web_search=web_search,
            client=client,
        )
