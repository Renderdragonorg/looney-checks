"""Runs the licensing research step through OpenRouter's chat-completions API.

Mirrors :class:`music_copyright_checker.ai_researcher.AIResearcher` (same
``research(request) -> (ResearchResult, meta)`` contract) but calls the
OpenRouter REST API directly instead of driving the ``opencode`` CLI agent.
OpenRouter's ``openrouter:web_search`` server tool provides the live web
research the prompt depends on.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .ai_researcher import AIResearchError, parse_research_response
from .errors import MusicCheckerError
from .models import LookupRequest, ResearchResult
from .openrouter_client import (
    DEFAULT_OPENROUTER_BASE_URL,
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_OPENROUTER_TIMEOUT,
    OpenRouterClient,
)
from .prompts import build_research_prompt


class OpenRouterResearcher:
    """Wraps :class:`OpenRouterClient` to run the licensing-research prompt."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_OPENROUTER_BASE_URL,
        model: Optional[str] = DEFAULT_OPENROUTER_MODEL,
        timeout: float = DEFAULT_OPENROUTER_TIMEOUT,
        web_search: bool = True,
        client: Optional[OpenRouterClient] = None,
    ) -> None:
        self._client = client or OpenRouterClient(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
        self._model = model or DEFAULT_OPENROUTER_MODEL
        self._timeout = timeout
        self._web_search = web_search

    def research(self, request: LookupRequest) -> tuple[ResearchResult, Dict[str, Any]]:
        """Run the research prompt and return (parsed result, raw run metadata)."""
        prompt_payload = request.to_dict()
        track_payload = prompt_payload.get("track")
        if isinstance(track_payload, dict):
            # Don't send source-debug payloads to the model.
            track_payload.pop("raw", None)
        prompt = build_research_prompt(prompt_payload)

        try:
            result = self._client.call(
                prompt,
                model=self._model,
                timeout=self._timeout,
                web_search=self._web_search,
            )
        except MusicCheckerError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise AIResearchError(f"OpenRouter research call failed: {exc}") from exc

        if not result.text:
            raise AIResearchError("OpenRouter research call returned no text output.")

        research_result = parse_research_response(result.text)
        meta = {
            "mode": "openrouter",
            "model": result.model or self._model,
            "session": result.session,
            "cost": result.cost,
            "tokens": result.tokens,
        }
        return research_result, meta
