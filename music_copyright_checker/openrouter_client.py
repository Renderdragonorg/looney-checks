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
from .exa_search import (
    DEFAULT_EXA_BASE_URL,
    DEFAULT_EXA_TIMEOUT,
    EXA_SEARCH_TOOL,
    ExaSearchClient,
    format_search_results,
)

# Load .env (OPENROUTER_API_KEY, ...) into os.environ without overriding real env.
load_env_file()

DEFAULT_OPENROUTER_MODEL = "openrouter/free"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_TIMEOUT = 300.0
OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"
MAX_NETWORK_ATTEMPTS = 3
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}
# Kept as a private alias for callers/tests that referenced it historically.
_RETRYABLE_STATUS = RETRYABLE_STATUS
# Search modes: OpenRouter's server tool, a client-side Exa function tool, or off.
SERVER_WEB_SEARCH = "server"
EXA_WEB_SEARCH = "exa"
DEFAULT_MAX_TOOL_ROUNDS = 6


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


def _post_json(
    url: str,
    body: Dict[str, Any],
    headers: Dict[str, str],
    timeout: float,
    *,
    provider: str = "OpenRouter",
    error_cls: type = OpenRouterError,
    api_key_env: str = OPENROUTER_API_KEY_ENV,
) -> Dict[str, Any]:
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
                raise error_cls(
                    f"{provider} rejected the API key ({exc.code}): {detail or 'unauthorized'}. "
                    f"Check {api_key_env}."
                ) from exc
            raise error_cls(f"{provider} request failed ({exc.code}): {detail or exc.reason}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt + 1 < MAX_NETWORK_ATTEMPTS:
                time.sleep(0.75 * (attempt + 1))
                continue
            raise error_cls(f"Could not reach the {provider} API: {exc}") from exc
    else:  # pragma: no cover - loop always breaks or raises
        raise error_cls(f"Could not reach the {provider} API: {last_error}")

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise error_cls(f"{provider} returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise error_cls(f"{provider} returned a non-object response.")
    error = payload.get("error")
    if error:
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise error_cls(f"{provider} API error: {message}")
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


def _first_message(payload: Dict[str, Any]) -> Dict[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return {}
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    return message if isinstance(message, dict) else {}


def _usage_cost(payload: Dict[str, Any]) -> Optional[float]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    cost = usage.get("cost")
    return float(cost) if isinstance(cost, (int, float)) else None


def _accumulate_usage(totals: Dict[str, Any], payload: Dict[str, Any]) -> None:
    """Add one completion's token usage into the running per-call totals."""
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    details = usage.get("completion_tokens_details")
    details = details if isinstance(details, dict) else {}
    for key, value in (
        ("input", usage.get("prompt_tokens")),
        ("output", usage.get("completion_tokens")),
        ("total", usage.get("total_tokens")),
        ("reasoning", details.get("reasoning_tokens")),
    ):
        if isinstance(value, (int, float)):
            totals[key] = (totals.get(key) or 0) + value


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
    """Minimal OpenRouter chat-completions client used by the researcher.

    ``supports_server_web_search`` marks providers that understand OpenRouter's
    ``openrouter:web_search`` server tool. Providers that do not (a generic
    OpenAI-compatible endpoint) automatically fall back to a client-side Exa
    function tool, which requires ``EXA_API_KEY``.
    """

    provider_label = "OpenRouter"
    error_class = OpenRouterError
    api_key_env = OPENROUTER_API_KEY_ENV
    default_model = DEFAULT_OPENROUTER_MODEL
    supports_server_web_search = True

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_OPENROUTER_BASE_URL,
        timeout: float = DEFAULT_OPENROUTER_TIMEOUT,
        app_name: Optional[str] = "music-copyright-checker",
        app_url: Optional[str] = None,
        search_backend: Optional[str] = None,
        exa_api_key: Optional[str] = None,
        exa_base_url: str = DEFAULT_EXA_BASE_URL,
        exa_timeout: Optional[float] = None,
        exa_api_key_env: str = "EXA_API_KEY",
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._app_name = app_name
        self._app_url = app_url
        self._search_backend = search_backend
        self._exa_api_key = exa_api_key
        self._exa_base_url = exa_base_url
        self._exa_timeout = exa_timeout if exa_timeout is not None else DEFAULT_EXA_TIMEOUT
        self._exa_api_key_env = exa_api_key_env
        self._max_tool_rounds = max(1, int(max_tool_rounds))

    @property
    def mode(self) -> str:
        return "openrouter"

    def _resolve_key(self) -> str:
        key = (self._api_key or os.environ.get(self.api_key_env) or "").strip()
        if not key:
            raise self.error_class(
                f"{self.provider_label} API key is not configured. Set {self.api_key_env} or pass "
                "an explicit api_key=... to Pipeline."
            )
        return key

    def _extra_body(self) -> Dict[str, Any]:
        """Provider-specific request-body fields (overridden by subclasses)."""
        return {}

    def _extra_headers(self) -> Dict[str, str]:
        """Provider-specific request headers (overridden by subclasses)."""
        return {}

    def call(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        system: Optional[str] = None,
        web_search: bool = True,
        search_backend: Optional[str] = None,
        extra_tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
    ) -> OpenRouterResult:
        """Run one chat completion and return the normalized result.

        ``search_backend`` is one of ``"auto"`` (default: server tool when the
        provider supports it, otherwise Exa), ``"server"`` (OpenRouter's
        ``openrouter:web_search``), ``"exa"`` (client-side Exa function tool),
        or ``"none"``. ``web_search=False`` disables search regardless.
        """
        model = model or self.default_model
        timeout = timeout if timeout is not None else self._timeout
        resolved_search = self._resolve_search_backend(web_search, search_backend)

        messages: List[Dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        tools: List[Dict[str, Any]] = []
        if resolved_search == SERVER_WEB_SEARCH:
            tools.append({"type": "openrouter:web_search", "parameters": {"max_results": 5}})
        elif resolved_search == EXA_WEB_SEARCH:
            tools.append(dict(EXA_SEARCH_TOOL))
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
        body.update(self._extra_body())

        headers = self._headers()
        # Fail fast on a missing Exa key before spending a completion.
        searcher = self._exa_searcher(timeout) if resolved_search == EXA_WEB_SEARCH else None
        payload = self._invoke(body, headers, timeout)
        if searcher is not None:
            return self._exa_tool_loop(body, headers, timeout, model, payload, searcher)
        return self._parse(payload, model)

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._resolve_key()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._app_url:
            headers["HTTP-Referer"] = self._app_url
        if self._app_name:
            headers["X-Title"] = self._app_name
        headers.update(self._extra_headers())
        return headers

    def _invoke(self, body: Dict[str, Any], headers: Dict[str, str], timeout: float) -> Dict[str, Any]:
        return _post_json(
            f"{self._base_url}/chat/completions",
            body,
            headers,
            timeout,
            provider=self.provider_label,
            error_cls=self.error_class,
            api_key_env=self.api_key_env,
        )

    def _resolve_search_backend(self, web_search: bool, search_backend: Optional[str]) -> Optional[str]:
        if not web_search:
            return None
        mode = (search_backend or self._search_backend or "auto").strip().lower()
        if mode in {"none", "off", "disabled"}:
            return None
        if mode == "auto":
            return SERVER_WEB_SEARCH if self.supports_server_web_search else EXA_WEB_SEARCH
        if mode == SERVER_WEB_SEARCH:
            if not self.supports_server_web_search:
                raise self.error_class(
                    f"{self.provider_label} does not support the openrouter:web_search server "
                    "tool; use search_backend='exa' for a client-side Exa web search."
                )
            return SERVER_WEB_SEARCH
        if mode == EXA_WEB_SEARCH:
            return EXA_WEB_SEARCH
        raise self.error_class(
            f"Unknown web search backend {mode!r}; expected 'auto', 'server', 'exa', or 'none'."
        )

    def _exa_searcher(self, timeout: float) -> ExaSearchClient:
        searcher = ExaSearchClient(
            api_key=self._exa_api_key,
            base_url=self._exa_base_url,
            timeout=self._exa_timeout,
            api_key_env=self._exa_api_key_env,
        )
        searcher.ensure_configured()
        return searcher

    def _exa_tool_loop(
        self,
        body: Dict[str, Any],
        headers: Dict[str, str],
        timeout: float,
        model: str,
        payload: Dict[str, Any],
        searcher: ExaSearchClient,
    ) -> OpenRouterResult:
        """Drive Exa function calls until the model produces a final answer."""
        messages = list(body["messages"])
        tokens: Dict[str, Any] = {"input": 0, "output": 0, "total": 0, "reasoning": 0}
        cost_total = 0.0
        cost_seen = False
        annotations: List[Dict[str, Any]] = []
        final_payload: Optional[Dict[str, Any]] = None

        for _ in range(self._max_tool_rounds):
            message = _first_message(payload)
            _accumulate_usage(tokens, payload)
            delta = _usage_cost(payload)
            if delta is not None:
                cost_total += delta
                cost_seen = True
            if isinstance(message.get("annotations"), list):
                annotations.extend(message["annotations"])

            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list) or not tool_calls:
                final_payload = payload
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                }
            )
            for tool_call in tool_calls:
                messages.append(self._run_tool_call(searcher, tool_call))
            body = {**body, "messages": messages}
            payload = self._invoke(body, headers, timeout)
            final_payload = payload

        if final_payload is None:  # pragma: no cover - loop always sets it
            raise self.error_class(f"{self.provider_label} returned no completion.")
        if _first_message(final_payload).get("tool_calls"):
            raise self.error_class(
                f"{self.provider_label} exceeded {self._max_tool_rounds} web-search rounds "
                "without producing a final answer."
            )

        result = self._parse(final_payload, model)
        result.tokens = tokens
        if cost_seen:
            result.cost = round(cost_total, 8)
        if annotations:
            result.annotations = annotations
        return result

    def _run_tool_call(self, searcher: ExaSearchClient, tool_call: Any) -> Dict[str, Any]:
        function = tool_call.get("function") if isinstance(tool_call, dict) else None
        function = function if isinstance(function, dict) else {}
        name = function.get("name")
        call_id = tool_call.get("id") if isinstance(tool_call, dict) else None

        if name == "web_search":
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except (TypeError, ValueError):
                    arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            query = arguments.get("query")
            num_results = arguments.get("num_results")
            try:
                results = searcher.search(
                    str(query or ""),
                    num_results=num_results if num_results is not None else 5,
                )
            except Exception as exc:  # feed the failure back so the model can adapt
                content = f"Web search failed: {exc}"
            else:
                content = format_search_results(results)
        else:
            content = f"Unknown tool: {name!r}"

        return {
            "role": "tool",
            "tool_call_id": call_id,
            "name": name or "tool",
            "content": content,
        }

    @classmethod
    def _parse(cls, payload: Dict[str, Any], requested_model: str) -> OpenRouterResult:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise cls.error_class(f"{cls.provider_label} returned no completion choices.")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        message = message if isinstance(message, dict) else {}
        text = _content_to_text(message.get("content")).strip()
        if not text:
            raise cls.error_class(f"{cls.provider_label} returned an empty completion.")

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
