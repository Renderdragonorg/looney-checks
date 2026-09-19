"""Generic OpenAI-compatible chat-completions backend.

Most hosted models speak the OpenAI ``/chat/completions`` shape but do not
implement OpenRouter's ``openrouter:web_search`` server tool. This backend
targets any such endpoint and uses the client-side Exa function tool for live
web research (``EXA_API_KEY`` required when web search is enabled).
"""

from __future__ import annotations

import os
from typing import Optional

from .env import load_env_file
from .errors import OpenAICompatibleError
from .exa_search import DEFAULT_EXA_BASE_URL
from .openrouter_client import OpenRouterClient

# Load OPENAI_COMPAT_* (etc.) into os.environ without overriding real env.
load_env_file()

DEFAULT_OPENAI_COMPAT_API_KEY_ENV = "OPENAI_COMPAT_API_KEY"
DEFAULT_OPENAI_COMPAT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_COMPAT_TIMEOUT = 300.0


class OpenAICompatibleClient(OpenRouterClient):
    """Chat-completions client for any OpenAI-compatible endpoint.

    The base URL and key env var are configurable so the same class can target
    OpenAI, Groq, Together, a local gateway, or any other compatible API.
    """

    provider_label = "OpenAI-compatible"
    error_class = OpenAICompatibleError
    api_key_env = DEFAULT_OPENAI_COMPAT_API_KEY_ENV
    default_model = ""
    # These endpoints do not know the openrouter:web_search server tool, so the
    # default search mode is the client-side Exa function tool.
    supports_server_web_search = False

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = DEFAULT_OPENAI_COMPAT_TIMEOUT,
        api_key_env: Optional[str] = None,
        app_name: Optional[str] = "music-copyright-checker",
        app_url: Optional[str] = None,
        search_backend: Optional[str] = None,
        exa_api_key: Optional[str] = None,
        exa_base_url: str = DEFAULT_EXA_BASE_URL,
        exa_timeout: Optional[float] = None,
        exa_api_key_env: str = "EXA_API_KEY",
        max_tool_rounds: Optional[int] = None,
    ) -> None:
        resolved_base_url = base_url or os.environ.get("OPENAI_COMPAT_BASE_URL") or DEFAULT_OPENAI_COMPAT_BASE_URL
        resolved_model = model or os.environ.get("OPENAI_COMPAT_MODEL") or ""
        if not resolved_model:
            raise OpenAICompatibleError(
                "An OpenAI-compatible model is required. Pass an explicit model=, set "
                "OPENAI_COMPAT_MODEL, or use --model."
            )
        super().__init__(
            api_key=api_key,
            base_url=resolved_base_url,
            timeout=timeout,
            app_name=app_name,
            app_url=app_url,
            search_backend=search_backend,
            exa_api_key=exa_api_key,
            exa_base_url=exa_base_url,
            exa_timeout=exa_timeout,
            exa_api_key_env=exa_api_key_env,
            **({} if max_tool_rounds is None else {"max_tool_rounds": max_tool_rounds}),
        )
        # Instance attributes shadow the class defaults used by the base client.
        self.api_key_env = api_key_env or DEFAULT_OPENAI_COMPAT_API_KEY_ENV
        self.default_model = resolved_model

    @property
    def mode(self) -> str:
        return "openai-compatible"
