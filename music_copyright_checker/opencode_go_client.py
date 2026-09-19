"""Direct OpenCode Go ("zen/go") chat-completions client.

OpenCode Go exposes an OpenRouter-compatible REST API at
``https://opencode.ai/zen/go/v1``, so this reuses
:class:`~music_copyright_checker.openrouter_client.OpenRouterClient` and only
changes the provider-specific details:

* auth comes from ``OPENCODE_GO_API_KEY`` (or an explicit ``api_key=``);
* requests must carry a non-default ``User-Agent`` plus an
  ``x-opencode-session`` header — the endpoint sits behind Cloudflare, which
  rejects the stock Python user-agent with error 1010;
* the default model (``mimo-v2.5``) is a reasoning model, so reasoning is
  disabled by default: it keeps replies fast and makes sure the visible
  ``content`` (the strict JSON we parse) is not starved of output tokens.

Web research still works because the endpoint accepts the same
``openrouter:web_search`` server tool and returns ``url_citation``
annotations.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from .env import load_env_file
from .errors import OpenCodeGoError
from .openrouter_client import OpenRouterClient

# Load OPENCODE_GO_API_KEY (etc.) into os.environ without overriding real env.
load_env_file()

DEFAULT_OPENCODE_GO_MODEL = "mimo-v2.5"
DEFAULT_OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
DEFAULT_OPENCODE_GO_TIMEOUT = 300.0
DEFAULT_OPENCODE_GO_USER_AGENT = "music-copyright-checker/0.3.1"
# The research reply is a large JSON document; without an explicit cap the
# provider default can truncate it mid-string, which then fails JSON parsing.
DEFAULT_OPENCODE_GO_MAX_TOKENS = 8_000
OPENCODE_GO_API_KEY_ENV = "OPENCODE_GO_API_KEY"


class OpenCodeGoClient(OpenRouterClient):
    """OpenCode Go chat-completions client used by the researcher."""

    provider_label = "OpenCode Go"
    error_class = OpenCodeGoError
    api_key_env = OPENCODE_GO_API_KEY_ENV
    default_model = DEFAULT_OPENCODE_GO_MODEL

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_OPENCODE_GO_BASE_URL,
        timeout: float = DEFAULT_OPENCODE_GO_TIMEOUT,
        session: Optional[str] = None,
        user_agent: Optional[str] = DEFAULT_OPENCODE_GO_USER_AGENT,
        disable_reasoning: bool = True,
        max_tokens: Optional[int] = DEFAULT_OPENCODE_GO_MAX_TOKENS,
        search_backend: Optional[str] = None,
        exa_api_key: Optional[str] = None,
        exa_base_url: Optional[str] = None,
        exa_timeout: Optional[float] = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            search_backend=search_backend,
            exa_api_key=exa_api_key,
            **({} if exa_base_url is None else {"exa_base_url": exa_base_url}),
            exa_timeout=exa_timeout,
        )
        self._session = session or uuid.uuid4().hex
        self._user_agent = user_agent
        self._disable_reasoning = disable_reasoning
        self._max_tokens = max_tokens

    @property
    def mode(self) -> str:
        return "opencode-go"

    def _extra_body(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        if self._disable_reasoning:
            body["reasoning"] = {"enabled": False}
        if self._max_tokens:
            body["max_tokens"] = self._max_tokens
        return body

    def _extra_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self._user_agent:
            headers["User-Agent"] = self._user_agent
        if self._session:
            headers["x-opencode-session"] = self._session
        return headers
