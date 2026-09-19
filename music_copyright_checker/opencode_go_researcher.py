"""Runs the licensing research step through the OpenCode Go REST API.

Mirrors :class:`~music_copyright_checker.openrouter_researcher.OpenRouterResearcher`
(same ``research(request) -> (ResearchResult, meta)`` contract) but calls the
OpenCode Go endpoint with its required headers and the ``mimo-v2.5`` model.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .opencode_go_client import (
    DEFAULT_OPENCODE_GO_BASE_URL,
    DEFAULT_OPENCODE_GO_MODEL,
    DEFAULT_OPENCODE_GO_TIMEOUT,
    OpenCodeGoClient,
)
from .openrouter_researcher import OpenRouterResearcher


class OpenCodeGoResearcher(OpenRouterResearcher):
    """Wraps :class:`OpenCodeGoClient` to run the licensing-research prompt."""

    provider_label = "OpenCode Go"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_OPENCODE_GO_BASE_URL,
        model: Optional[str] = DEFAULT_OPENCODE_GO_MODEL,
        timeout: float = DEFAULT_OPENCODE_GO_TIMEOUT,
        web_search: bool = True,
        search_backend: Optional[str] = None,
        exa_api_key: Optional[str] = None,
        exa_base_url: Optional[str] = None,
        exa_timeout: Optional[float] = None,
        client: Optional[OpenCodeGoClient] = None,
    ) -> None:
        if client is None:
            client_kwargs: Dict[str, Any] = {
                "api_key": api_key,
                "base_url": base_url,
                "timeout": timeout,
                "search_backend": search_backend,
                "exa_api_key": exa_api_key,
            }
            if exa_base_url is not None:
                client_kwargs["exa_base_url"] = exa_base_url
            if exa_timeout is not None:
                client_kwargs["exa_timeout"] = exa_timeout
            client = OpenCodeGoClient(**client_kwargs)
        super().__init__(
            model=model,
            timeout=timeout,
            web_search=web_search,
            client=client,
        )
