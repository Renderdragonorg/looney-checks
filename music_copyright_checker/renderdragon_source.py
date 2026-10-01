"""Resolves (and builds) RenderDragon music links into normalized track metadata.

RenderDragon exposes a single HTTPS endpoint that turns track metadata into a
link other apps can consume:

``GET https://renderdragon.org/api/music-link?name=...&url=...&credits=...``

Programmatic callers must send ``Accept: application/json``; browsers that send
``Accept: text/html`` get a 302 redirect to the human-facing resources page.
This module always asks for JSON, verifies the response, and folds the result
into the same :class:`~music_copyright_checker.models.TrackMetadata` /
:class:`~music_copyright_checker.models.TrackCredits` shape every other source
produces, so the AI research step does not need to know where it came from.

Only ``raw.githubusercontent.com`` URLs from the allowlisted RenderDragon repos
are accepted by the endpoint (it is not an open proxy); :func:`build_music_link`
enforces the same list before it will generate a link.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import time
from typing import Any, Dict, Mapping, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from .errors import (
    InvalidRenderDragonLinkError,
    RenderDragonAPIError,
    RenderDragonLookupError,
)
from .models import Credit, TrackCredits, TrackMetadata

RENDERDRAGON_API_BASE = "https://renderdragon.org/api/music-link"
RENDERDRAGON_HOST = "renderdragon.org"
RENDERDRAGON_API_PATH = "/api/music-link"
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_NETWORK_ATTEMPTS = 3
MAX_RAW_PAYLOAD_CHARS = 4000

ALLOWLISTED_SOURCE_PREFIXES = (
    "https://raw.githubusercontent.com/Yxmura/resources_renderdragon/",
    "https://raw.githubusercontent.com/Coder-soft/Minecraft-Creator-Safe-Playlist/",
)

_GITHUB_BLOB_RE = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/blob/(?P<ref>.+)$",
    re.IGNORECASE,
)


def to_raw_github_url(url: str) -> str:
    """Convert a ``github.com/.../blob/...`` URL to its raw.githubusercontent form.

    RenderDragon accepts only raw file URLs, so a viewer URL copied from GitHub
    is rewritten; anything else is returned unchanged.
    """
    candidate = (url or "").strip()
    match = _GITHUB_BLOB_RE.match(candidate)
    if not match:
        return candidate
    return (
        f"https://raw.githubusercontent.com/{match.group('owner')}/"
        f"{match.group('repo')}/{match.group('ref')}"
    )


def is_allowlisted_source(url: Optional[str]) -> bool:
    """Return True when ``url`` is a raw GitHub file in an allowlisted repo."""
    candidate = (url or "").strip()
    if not candidate:
        return False
    return any(candidate.startswith(prefix) for prefix in ALLOWLISTED_SOURCE_PREFIXES)


def _first(track: Mapping[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = track.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def build_music_link(
    track: Mapping[str, Any],
    *,
    base_url: str = RENDERDRAGON_API_BASE,
    validate: bool = True,
) -> str:
    """Build a RenderDragon music link from a resource/metadata mapping.

    Accepts either RenderDragon resource API field names (``title``,
    ``download_url``, ``credit``, ``filetype``, ``filename``) or the music-link
    query names (``name``, ``url``, ``credits``, ``ext``, ``file``). The ``url``
    is rewritten from a GitHub blob URL to its raw form and, unless
    ``validate=False``, must be in the allowlist.
    """
    name = _first(track, "name", "title")
    url = _first(track, "url", "download_url")
    if url:
        url = to_raw_github_url(url)
        if validate and not is_allowlisted_source(url):
            raise InvalidRenderDragonLinkError(
                "Only allowlisted raw.githubusercontent.com URLs can be used as the "
                "RenderDragon music-link 'url'."
            )
    params: Dict[str, str] = {}
    for key, value in (
        ("name", name),
        ("url", url),
        ("credits", _first(track, "credits", "credit")),
        ("category", _first(track, "category")),
        ("ext", _first(track, "ext", "filetype")),
        ("file", _first(track, "file", "filename")),
        ("id", _first(track, "id")),
    ):
        if value is not None:
            params[key] = value
    return f"{base_url}?{urlencode(params)}"


def parse_music_link(link: str) -> Dict[str, str]:
    """Parse a RenderDragon music-link URL into its query parameters.

    Raises:
        InvalidRenderDragonLinkError: if the value is not a renderdragon.org
            ``/api/music-link`` URL.
    """
    candidate = (link or "").strip()
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host != RENDERDRAGON_HOST:
        raise InvalidRenderDragonLinkError(
            f"Not a RenderDragon music link: {link!r}. Expected an "
            f"https://{RENDERDRAGON_HOST}{RENDERDRAGON_API_PATH}? URL."
        )
    if parsed.path.rstrip("/") != RENDERDRAGON_API_PATH:
        raise InvalidRenderDragonLinkError(
            f"Not a RenderDragon music link: {link!r}. Expected path {RENDERDRAGON_API_PATH}."
        )
    query = parse_qs(parsed.query, keep_blank_values=False)
    return {key: values[0] for key, values in query.items() if values}


def renderdragon_identity(params: Mapping[str, str]) -> str:
    """Return a stable cache identity for a parsed music link."""
    track_id = params.get("id")
    if track_id:
        return f"id:{track_id}"
    raw_url = params.get("url") or ""
    if raw_url:
        return f"url:{raw_url}"
    digest = hashlib.sha256(
        json.dumps(dict(sorted(params.items())), separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"params:{digest}"


def _ssl_context() -> Optional[ssl.SSLContext]:
    """CA-trusting TLS context, preferring certifi's bundle (macOS/frozen builds)."""
    try:
        import certifi

        cafile = certifi.where()
        if os.path.isfile(cafile):
            return ssl.create_default_context(cafile=cafile)
    except (ImportError, OSError):
        pass
    return ssl.create_default_context()


def _http_error_message(exc: HTTPError) -> str:
    if exc.code == 400:
        return "RenderDragon rejected the track: the 'url' is not in the allowlist."
    if exc.code == 422:
        return "RenderDragon rejected the track: the 'url' parameter is missing."
    if exc.code == 405:
        return "RenderDragon music links only accept GET requests."
    detail = ""
    try:
        data = json.loads(exc.read().decode("utf-8", "replace"))
        if isinstance(data, dict):
            detail = str(data.get("error") or data.get("detail") or "")
    except (ValueError, AttributeError):
        detail = ""
    return f"RenderDragon music-link request failed ({exc.code}): {detail or exc.reason}"


def _request_json(url: str, timeout: float) -> Dict[str, Any]:
    """GET a music link as JSON, retrying transient TLS/network drops."""
    request = Request(
        url,
        headers={
            "User-Agent": "music-copyright-checker/0.1",
            "Accept": "application/json",
        },
    )
    context = _ssl_context()
    last_error: Optional[BaseException] = None
    body: Optional[bytes] = None
    for attempt in range(MAX_NETWORK_ATTEMPTS):
        try:
            with urlopen(request, timeout=timeout, context=context) as response:
                body = response.read()
            break
        except HTTPError as exc:
            raise RenderDragonAPIError(_http_error_message(exc)) from exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt + 1 < MAX_NETWORK_ATTEMPTS:
                time.sleep(0.5 * (attempt + 1))
    if body is None:
        raise RenderDragonLookupError(
            f"Could not reach the RenderDragon music-link endpoint: {last_error}"
        ) from last_error

    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RenderDragonLookupError(
            f"Invalid JSON returned by the RenderDragon music-link endpoint: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise RenderDragonLookupError(
            "Unexpected non-object response from the RenderDragon music-link endpoint."
        )
    return payload


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_music_link(
    payload: Mapping[str, Any],
    *,
    track_id: Optional[str] = None,
) -> Tuple[TrackMetadata, TrackCredits]:
    """Fold a RenderDragon music-link JSON response into metadata + credits.

    The response body does not echo the resource ``id`` (it is only a query
    parameter used for the human redirect), so callers that have the parsed
    link should pass ``track_id`` to retain it for caching/identity.
    """
    if not isinstance(payload, Mapping):
        raise RenderDragonLookupError("Unexpected RenderDragon music-link response type.")

    name = payload.get("name")
    credits_text = payload.get("credits")
    credits_text = credits_text.strip() if isinstance(credits_text, str) else ""
    direct_url = payload.get("direct_url")
    website_url = payload.get("website_url")
    filename = payload.get("filename")
    content_type = payload.get("content_type")
    raw_id = payload.get("id") or track_id

    available = payload.get("available")
    available_bool = available if isinstance(available, bool) else None

    raw = {
        key: payload.get(key)
        for key in (
            "service",
            "name",
            "credits",
            "category",
            "extension",
            "filename",
            "direct_url",
            "raw_reference",
            "website_url",
            "source",
            "available",
            "size",
            "content_type",
        )
    }
    raw["id"] = raw_id
    raw = json.loads(json.dumps(raw, ensure_ascii=False))

    metadata = TrackMetadata(
        name=name if isinstance(name, str) else None,
        artists=[credits_text] if credits_text else [],
        category=payload.get("category") if isinstance(payload.get("category"), str) else None,
        renderdragon_id=str(raw_id) if raw_id not in (None, "") else None,
        audio_url=direct_url if isinstance(direct_url, str) else None,
        website_url=website_url if isinstance(website_url, str) else None,
        filename=filename if isinstance(filename, str) else None,
        available=available_bool,
        size_bytes=_int_or_none(payload.get("size")),
        content_type=content_type if isinstance(content_type, str) else None,
        raw=raw,
    )
    performers = [Credit(name=credits_text, role="Credit")] if credits_text else []
    track_credits = TrackCredits(
        available=bool(performers),
        source_note=(
            "RenderDragon music-link response: name, credit string, category, and the "
            "resolved raw audio URL. It does not expose ISRC or songwriter/publisher "
            "credits; the AI research step fills those in."
        ),
        performers=performers,
    )
    return metadata, track_credits


class RenderDragonSource:
    """High-level entry point: music link (or params) -> metadata + credits."""

    def __init__(
        self,
        *,
        base_url: str = RENDERDRAGON_API_BASE,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout

    def fetch(self, link: str) -> Tuple[TrackMetadata, TrackCredits]:
        """Parse a RenderDragon music link, resolve it, and normalize the response."""
        return self.fetch_params(parse_music_link(link))

    def fetch_params(self, params: Mapping[str, str]) -> Tuple[TrackMetadata, TrackCredits]:
        """Resolve already-parsed music-link parameters."""
        if not params.get("url"):
            raise InvalidRenderDragonLinkError(
                "A RenderDragon music link must include a 'url' query parameter."
            )
        payload = _request_json(f"{self._base_url}?{urlencode(dict(params))}", self._timeout)
        metadata, credits = normalize_music_link(payload, track_id=params.get("id"))
        if metadata.available is False:
            raise RenderDragonLookupError(
                f"RenderDragon could not verify this track as available: {metadata.name or params.get('name') or params.get('url')}."
            )
        return metadata, credits
