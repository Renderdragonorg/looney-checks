"""Direct OpenRouter chat-completions client.

This is the API-based replacement for the ``opencode`` CLI agent: instead of
spawning a local agent process, we call OpenRouter's OpenAI-compatible REST API
at ``https://openrouter.ai/api/v1/chat/completions``.

The default model is OpenRouter's **free model router** (``openrouter/free``),
which picks a free, tool-capable model per request. Web research is enabled
with OpenRouter's server-side ``openrouter:web_search`` tool, so the model can
still look up rights holders, publishers and licensing pages without any local
browser/agent tooling.

Auth comes from the ``OPENROUTER_API_KEY`` environment variable (or an explicit
``api_key=``), so no key needs to be committed to the repo.
"""

from __future__ import annotations

import json
import os
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .env import load_env_file
from .errors import OpenRouterError

# Load .env (OPENROUTER_API_KEY, ...) into os.environ without overriding real env.
load_env_file()

DEFAULT_OPENROUTER_MODEL = "openrouter/free"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_TIMEOUT = 300.0
OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"
MAX_NETWORK_ATTEMPTS = 3
_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


@dataclass
class OpenRouterResult:
    """The parts of an OpenRouter completion the researcher cares about."""

    text: str
    model: Optional[str] = None
    session: Optional[str] = None
    cost: Optional[float] = None
    tokens: Dict[str, Any] = field(default_factory=dict)
    annotations: List[Dict[str, Any]] = field(default_factory=list)


def _ssl_context() -> ssl.SSLContext:
    """Prefer certifi's CA bundle (python.org macOS builds miss the keychain)."""
    try:
        import certifi

        cafile = certifi.where()
        if os.path.isfile(cafile):
            return ssl.create_default_context(cafile=cafile)
    except (ImportError, OSError):
        pass
    return ssl.create_default_context()


def _post_json(url: str, body: Dict[str, Any], headers: Dict[str, str], timeout: float) -> Dict[str, Any]:
    """POST JSON, retrying transient network/rate-limit failures, and decode the reply."""
    data = json.dumps(body).encode("utf-8")
    context = _ssl_context()
    last_error: Optional[BaseException] = None
    for attempt in range(MAX_NETWORK_ATTEMPTS):
        request = Request(url, data=data, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=timeout, context=context) as response:
                raw = response.read()
            break
        except HTTPError as exc:
            detail = _error_detail(exc)
            if exc.code in _RETRYABLE_STATUS and attempt + 1 < MAX_NETWORK_ATTEMPTS:
                last_error = exc
                time.sleep(0.75 * (attempt + 1))
                continue
            if exc.code in {401, 403}:
                raise OpenRouterError(
                    f"OpenRouter rejected the API key ({exc.code}): {detail or 'unauthorized'}. "
                    f"Check {OPENROUTER_API_KEY_ENV}."
                ) from exc
            raise OpenRouterError(f"OpenRouter request failed ({exc.code}): {detail or exc.reason}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt + 1 < MAX_NETWORK_ATTEMPTS:
                time.sleep(0.75 * (attempt + 1))
                continue
            raise OpenRouterError(f"Could not reach the OpenRouter API: {exc}") from exc
    else:  # pragma: no cover - loop always breaks or raises
        raise OpenRouterError(f"Could not reach the OpenRouter API: {last_error}")

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise OpenRouterError(f"OpenRouter returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise OpenRouterError("OpenRouter returned a non-object response.")
    error = payload.get("error")
    if error:
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise OpenRouterError(f"OpenRouter API error: {message}")
    return payload


def _error_detail(exc: HTTPError) -> str:
    try:
        data = json.loads(exc.read().decode("utf-8", "replace"))
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict):
            return error.get("message") or ""
        if isinstance(error, str):
            return error
    except (ValueError, AttributeError, OSError):
        pass
    return ""


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts)
    return ""


class OpenRouterClient:
    """Minimal OpenRouter chat-completions client used by the researcher."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_OPENROUTER_BASE_URL,
        timeout: float = DEFAULT_OPENROUTER_TIMEOUT,
        app_name: Optional[str] = "music-copyright-checker",
        app_url: Optional[str] = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._app_name = app_name
        self._app_url = app_url

    @property
    def mode(self) -> str:
        return "openrouter"

    def _resolve_key(self) -> str:
        key = (self._api_key or os.environ.get(OPENROUTER_API_KEY_ENV) or "").strip()
        if not key:
            raise OpenRouterError(
                f"OpenRouter API key is not configured. Set {OPENROUTER_API_KEY_ENV} or pass "
                "openrouter_api_key=... to Pipeline."
            )
        return key

    def call(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        system: Optional[str] = None,
        web_search: bool = True,
        extra_tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
    ) -> OpenRouterResult:
        """Run one chat completion and return the normalized result."""
        model = model or DEFAULT_OPENROUTER_MODEL
        timeout = timeout if timeout is not None else self._timeout

        messages: List[Dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        tools: List[Dict[str, Any]] = []
        if web_search:
            tools.append({"type": "openrouter:web_search", "parameters": {"max_results": 5}})
        if extra_tools:
            tools.extend(extra_tools)

        body: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "usage": {"include": True},
        }
        if tools:
            body["tools"] = tools
        if temperature is not None:
            body["temperature"] = temperature

        headers = {
            "Authorization": f"Bearer {self._resolve_key()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._app_url:
            headers["HTTP-Referer"] = self._app_url
        if self._app_name:
            headers["X-Title"] = self._app_name

        payload = _post_json(f"{self._base_url}/chat/completions", body, headers, timeout)
        return self._parse(payload, model)

    @staticmethod
    def _parse(payload: Dict[str, Any], requested_model: str) -> OpenRouterResult:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenRouterError("OpenRouter returned no completion choices.")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        message = message if isinstance(message, dict) else {}
        text = _content_to_text(message.get("content")).strip()
        if not text:
            raise OpenRouterError("OpenRouter returned an empty completion.")

        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        details = usage.get("completion_tokens_details")
        details = details if isinstance(details, dict) else {}
        tokens = {
            "input": usage.get("prompt_tokens"),
            "output": usage.get("completion_tokens"),
            "total": usage.get("total_tokens"),
            "reasoning": details.get("reasoning_tokens"),
        }
        cost = usage.get("cost")
        annotations = message.get("annotations")
        return OpenRouterResult(
            text=text,
            model=payload.get("model") or requested_model,
            session=payload.get("id"),
            cost=float(cost) if isinstance(cost, (int, float)) else None,
            tokens=tokens,
            annotations=annotations if isinstance(annotations, list) else [],
        )
