"""Exa-backed web search, usable as a client-side tool for any chat provider.

OpenRouter and OpenCode Go expose a server-side ``openrouter:web_search`` tool,
but most OpenAI-compatible providers do not. For those we advertise a normal
function tool (``web_search``) and execute it ourselves against Exa's REST API
(<https://docs.exa.ai/>), feeding the results back to the model as tool output.

Auth comes from ``EXA_API_KEY`` (or an explicit ``api_key=``), so no key needs
to be committed to the repo. A missing key raises a clear
:class:`~music_copyright_checker.errors.ExaSearchError`, because a non-OpenRouter
provider cannot web-search without it.
"""

from __future__ import annotations

import json
import os
import ssl
import time
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .env import load_env_file
from .errors import ExaSearchError

# Load EXA_API_KEY (etc.) into os.environ without overriding real env.
load_env_file()

DEFAULT_EXA_BASE_URL = "https://api.exa.ai"
DEFAULT_EXA_API_KEY_ENV = "EXA_API_KEY"
DEFAULT_EXA_TIMEOUT = 30.0
DEFAULT_EXA_NUM_RESULTS = 5
MAX_EXA_RESULTS = 10
MAX_EXA_TEXT_CHARACTERS = 2_000
MAX_NETWORK_ATTEMPTS = 3
_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}

EXA_SEARCH_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the public web for up-to-date information (rights holders, publishers, "
            "licensing pages, official terms). Returns titles, URLs, and text excerpts. "
            "Use short, targeted queries and cite the URLs you rely on."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query, e.g. 'Pink Floyd Another Brick in the Wall licensing'.",
                },
                "num_results": {
                    "type": "integer",
                    "description": f"How many results to return (1-{MAX_EXA_RESULTS}). Defaults to {DEFAULT_EXA_NUM_RESULTS}.",
                    "minimum": 1,
                    "maximum": MAX_EXA_RESULTS,
                },
            },
            "required": ["query"],
        },
    },
}


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


def _clamp_results(value: Any) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return DEFAULT_EXA_NUM_RESULTS
    return max(1, min(MAX_EXA_RESULTS, count))


class ExaSearchClient:
    """Minimal Exa ``/search`` client used as a provider-agnostic web tool."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_EXA_BASE_URL,
        timeout: float = DEFAULT_EXA_TIMEOUT,
        api_key_env: str = DEFAULT_EXA_API_KEY_ENV,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._api_key_env = api_key_env

    @property
    def available(self) -> bool:
        return bool(self._api_key or os.environ.get(self._api_key_env))

    def ensure_configured(self) -> "ExaSearchClient":
        """Raise :class:`ExaSearchError` now if no API key is available."""
        self._resolve_key()
        return self

    def _resolve_key(self) -> str:
        key = (self._api_key or os.environ.get(self._api_key_env) or "").strip()
        if not key:
            raise ExaSearchError(
                "Exa web search is not configured. Set "
                f"{self._api_key_env} or pass an explicit exa_api_key=... . "
                "It is required for AI providers that do not support OpenRouter's "
                "server-side openrouter:web_search tool."
            )
        return key

    def search(
        self,
        query: str,
        *,
        num_results: int = DEFAULT_EXA_NUM_RESULTS,
        timeout: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Run one Exa search and return normalized result dictionaries."""
        query = (query or "").strip()
        if not query:
            return []
        body = {
            "query": query,
            "numResults": _clamp_results(num_results),
            "type": "auto",
            "contents": {"text": {"maxCharacters": MAX_EXA_TEXT_CHARACTERS}},
        }
        payload = self._post_json(
            f"{self._base_url}/search",
            body,
            headers={
                "x-api-key": self._resolve_key(),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=timeout if timeout is not None else self._timeout,
        )
        return _normalize_results(payload.get("results"))

    def _post_json(
        self,
        url: str,
        body: Dict[str, Any],
        *,
        headers: Dict[str, str],
        timeout: float,
    ) -> Dict[str, Any]:
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
                    raise ExaSearchError(
                        f"Exa rejected the API key ({exc.code}): {detail or 'unauthorized'}. "
                        f"Check {self._api_key_env}."
                    ) from exc
                raise ExaSearchError(f"Exa search failed ({exc.code}): {detail or exc.reason}") from exc
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt + 1 < MAX_NETWORK_ATTEMPTS:
                    time.sleep(0.75 * (attempt + 1))
                    continue
                raise ExaSearchError(f"Could not reach the Exa API: {exc}") from exc
        else:  # pragma: no cover - loop always breaks or raises
            raise ExaSearchError(f"Could not reach the Exa API: {last_error}")

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ExaSearchError(f"Exa returned invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ExaSearchError("Exa returned a non-object response.")
        error = payload.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise ExaSearchError(f"Exa API error: {message}")
        return payload


def _error_detail(exc: HTTPError) -> str:
    try:
        data = json.loads(exc.read().decode("utf-8", "replace"))
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict):
            return error.get("message") or data.get("message") or ""
        if isinstance(error, str):
            return error
        if isinstance(data, dict):
            return str(data.get("message") or "")
    except (ValueError, AttributeError, OSError):
        pass
    return ""


def _normalize_results(results: Any) -> List[Dict[str, Any]]:
    if not isinstance(results, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url:
            continue
        text = item.get("text") or item.get("summary") or ""
        normalized.append(
            {
                "title": item.get("title") or url,
                "url": url,
                "published_date": item.get("publishedDate"),
                "author": item.get("author"),
                "text": text if isinstance(text, str) else str(text),
            }
        )
    return normalized


def format_search_results(results: List[Dict[str, Any]]) -> str:
    """Render Exa results as compact plain text for the model's tool output."""
    if not results:
        return "No web results found. Try a different query."
    blocks = []
    for index, item in enumerate(results, start=1):
        lines = [f"[{index}] {item.get('title') or item.get('url')}", f"URL: {item.get('url')}"]
        if item.get("published_date"):
            lines.append(f"Published: {item['published_date']}")
        if item.get("author"):
            lines.append(f"Author: {item['author']}")
        snippet = " ".join(str(item.get("text") or "").split())
        if snippet:
            if len(snippet) > 1_500:
                snippet = snippet[:1_500] + "..."
            lines.append(snippet)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
