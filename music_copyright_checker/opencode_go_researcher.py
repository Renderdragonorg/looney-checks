"""Runs the licensing research step through the OpenCode Go REST API.

Mirrors :class:`~music_copyright_checker.openrouter_researcher.OpenRouterResearcher`
(same ``research(request) -> (ResearchResult, meta)`` contract) but calls the
OpenCode Go endpoint with its required headers and the ``mimo-v2.5`` model.
"""

from __future__ import annotations

from typing import Optional

from .openrouter_researcher import OpenRouterResearcher
from .opencode_go_client import (
    DEFAULT_OPENCODE_GO_BASE_URL,
    DEFAULT_OPENCODE_GO_MODEL,
    DEFAULT_OPENCODE_GO_TIMEOUT,
    OpenCodeGoClient,
)


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
        client: Optional[OpenCodeGoClient] = None,
    ) -> None:
        super().__init__(
            model=model,
            timeout=timeout,
            web_search=web_search,
            client=client or OpenCodeGoClient(api_key=api_key, base_url=base_url, timeout=timeout),
        )
