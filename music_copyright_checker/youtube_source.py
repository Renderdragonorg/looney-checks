"""Fetches music/video metadata from the YouTube Data API v3.

The YouTube Data API v3 (https://developers.google.com/youtube/v3/docs) is an
official, key-authenticated REST API. We use two read-only endpoints:

* ``videos.list`` (part=snippet,contentDetails,statistics,topicDetails) turns a
  video id into title, channel/uploader, description, tags, category, duration,
  publication date, view counts and the ``licensedContent`` flag.
* ``search.list`` (part=snippet, type=video, videoCategoryId=10) resolves a
  free-text song query to the best-matching music video id, mirroring the
  "paste a URL or a name" flow the Spotify source supports.

The API does not expose ISRCs or songwriter/publisher credits, so - exactly
like the Spotify credits path - we surface the best-effort signals YouTube does
give us (channel/uploader and the "Provided to YouTube by <label>" licensing
line many official uploads carry) and let the AI research step fill in the rest
from public rights sources.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import time
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from .errors import InvalidYouTubeURLError, YouTubeAPIError, YouTubeLookupError
from .models import Credit, TrackCredits, TrackMetadata

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_API_KEY_ENV = "YOUTUBE_API_KEY"
DEFAULT_TIMEOUT_SECONDS = 30.0
YOUTUBE_MUSIC_CATEGORY_ID = "10"
MAX_DESCRIPTION_CHARS = 2000
MAX_NETWORK_ATTEMPTS = 3

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)
_LABEL_RE = re.compile(r"Provided to YouTube by\s+(?P<label>[^\r\n]+)", re.IGNORECASE)

_YOUTUBE_CATEGORIES = {
    "1": "Film & Animation",
    "2": "Autos & Vehicles",
    "10": "Music",
    "20": "Gaming",
    "22": "People & Blogs",
    "23": "Comedy",
    "24": "Entertainment",
    "25": "News & Politics",
    "26": "Howto & Style",
    "27": "Education",
    "28": "Science & Technology",
    "29": "Nonprofits & Activism",
}


def resolve_api_key(api_key: Optional[str] = None) -> str:
    """Return the configured YouTube Data API v3 key (explicit arg > env)."""
    key = (api_key or os.environ.get(YOUTUBE_API_KEY_ENV) or "").strip()
    if not key:
        raise YouTubeLookupError(
            "No YouTube Data API v3 key configured. Set the YOUTUBE_API_KEY environment "
            "variable (or pass youtube_api_key=... to Pipeline)."
        )
    return key


def parse_video_id(url_or_id: str) -> str:
    """Extract a bare 11-character YouTube video id from a URL or raw id.

    Supports ``youtube.com/watch?v=...``, ``youtu.be/...``, ``/shorts/``,
    ``/embed/``, ``/live/``, ``/v/`` and ``music.youtube.com`` URLs, plus an
    already-bare video id.

    Raises:
        InvalidYouTubeURLError: if no video id could be found.
    """
    candidate = (url_or_id or "").strip()
    if _VIDEO_ID_RE.match(candidate):
        return candidate

    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    if host == "youtu.be" or host.endswith(".youtu.be"):
        video_id = parsed.path.lstrip("/").split("/", 1)[0]
        if _VIDEO_ID_RE.match(video_id):
            return video_id
    elif host == "youtube.com" or host.endswith(".youtube.com") or host.endswith(".youtube-nocookie.com"):
        query = parse_qs(parsed.query)
        video_id = (query.get("v") or [""])[0]
        if _VIDEO_ID_RE.match(video_id):
            return video_id
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live", "v"} and _VIDEO_ID_RE.match(parts[1]):
            return parts[1]

    raise InvalidYouTubeURLError(
        f"Could not parse a YouTube video id out of {url_or_id!r}. Expected a "
        "youtube.com/watch?v=... URL, a youtu.be/... URL, or a bare "
        "11-character video id."
    )


def _ssl_context() -> Optional[ssl.SSLContext]:
    """Build a CA-trusting TLS context, preferring certifi's bundle.

    python.org Python builds on macOS (and frozen PyInstaller binaries) don't
    read the system keychain, so stdlib ``urlopen`` fails certificate
    verification. certifi is a declared dependency, so use its CA file when
    available and otherwise fall back to the interpreter default.
    """
    try:
        import certifi

        cafile = certifi.where()
        if os.path.isfile(cafile):
            return ssl.create_default_context(cafile=cafile)
    except (ImportError, OSError):
        pass
    return ssl.create_default_context()


def _fetch_json_bytes(url: str, timeout: float) -> bytes:
    """GET a URL, retrying transient TLS/network drops (not API error responses)."""
    request = Request(
        url,
        headers={
            "User-Agent": "music-copyright-checker/0.1",
            "Accept": "application/json",
        },
    )
    context = _ssl_context()
    last_error: Optional[BaseException] = None
    for attempt in range(MAX_NETWORK_ATTEMPTS):
        try:
            with urlopen(request, timeout=timeout, context=context) as response:
                return response.read()
        except HTTPError:
            raise
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt + 1 < MAX_NETWORK_ATTEMPTS:
                time.sleep(0.5 * (attempt + 1))
    raise YouTubeLookupError(f"Could not reach the YouTube Data API: {last_error}") from last_error


def _request_json(path: str, params: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    """Call one YouTube Data API v3 endpoint and return its decoded JSON object."""
    url = f"{YOUTUBE_API_BASE}/{path}?{urlencode(params)}"
    try:
        body = _fetch_json_bytes(url, timeout)
    except HTTPError as exc:
        detail = ""
        try:
            data = json.loads(exc.read().decode("utf-8", "replace"))
            error = data.get("error") if isinstance(data, dict) else None
            if isinstance(error, dict):
                detail = error.get("message") or ""
        except (ValueError, AttributeError):
            detail = ""
        raise YouTubeAPIError(
            f"YouTube Data API request failed ({exc.code}): {detail or exc.reason}"
        ) from exc

    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise YouTubeLookupError(f"Invalid JSON returned by the YouTube Data API: {exc}") from exc

    if not isinstance(payload, dict):
        raise YouTubeLookupError("Unexpected non-object response from the YouTube Data API.")
    error = payload.get("error")
    if error:
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise YouTubeAPIError(f"YouTube Data API error: {message}")
    return payload


def iso8601_duration_to_ms(duration: Optional[str]) -> Optional[int]:
    """Convert a YouTube ISO-8601 duration (``PT3M21S``) to milliseconds."""
    if not duration or not isinstance(duration, str):
        return None
    match = _DURATION_RE.match(duration)
    if not match:
        return None
    parts = match.groupdict()
    seconds = (
        int(parts["days"] or 0) * 86400
        + int(parts["hours"] or 0) * 3600
        + int(parts["minutes"] or 0) * 60
        + float(parts["seconds"] or 0)
    )
    return int(seconds * 1000)


def _first_thumbnail(snippet: Dict[str, Any]) -> Optional[str]:
    thumbnails = snippet.get("thumbnails")
    if not isinstance(thumbnails, dict):
        return None
    for size in ("maxres", "standard", "high", "medium", "default"):
        node = thumbnails.get(size)
        if isinstance(node, dict) and node.get("url"):
            return node["url"]
    return None


def _category_from_snippet(snippet: Dict[str, Any], topic: Dict[str, Any]) -> Optional[str]:
    topics = topic.get("topicCategories")
    if isinstance(topics, list) and any("music" in str(t).lower() for t in topics):
        return "Music"
    category_id = snippet.get("categoryId")
    if category_id is None:
        return None
    return _YOUTUBE_CATEGORIES.get(str(category_id), f"youtube-category:{category_id}")


def normalize_video_info(raw: Dict[str, Any], video_id: str) -> TrackMetadata:
    """Turn a ``videos.list`` item into a :class:`TrackMetadata`.

    The API shape is stable, but every field is treated as optional so a
    partial response (region-blocked video, missing statistics, etc.) still
    yields useful metadata instead of raising.
    """
    snippet = raw.get("snippet") if isinstance(raw.get("snippet"), dict) else {}
    content = raw.get("contentDetails") if isinstance(raw.get("contentDetails"), dict) else {}
    statistics = raw.get("statistics") if isinstance(raw.get("statistics"), dict) else {}
    topic = raw.get("topicDetails") if isinstance(raw.get("topicDetails"), dict) else {}

    title = snippet.get("title")
    channel = snippet.get("channelTitle")
    channel_id = snippet.get("channelId")
    description = snippet.get("description")
    if isinstance(description, str) and len(description) > MAX_DESCRIPTION_CHARS:
        description = description[:MAX_DESCRIPTION_CHARS]

    tags = [tag for tag in (snippet.get("tags") or []) if isinstance(tag, str)]
    published_at = snippet.get("publishedAt") or content.get("publishedAt")
    licensed = content.get("licensedContent")
    licensed_content = licensed if isinstance(licensed, bool) else None

    view_count: Optional[int] = None
    if statistics.get("viewCount") is not None:
        try:
            view_count = int(statistics["viewCount"])
        except (TypeError, ValueError):
            view_count = None

    return TrackMetadata(
        name=title,
        artists=[channel] if isinstance(channel, str) and channel else [],
        release_date=published_at,
        duration_ms=iso8601_duration_to_ms(content.get("duration")),
        youtube_id=video_id,
        youtube_url=f"https://www.youtube.com/watch?v={video_id}",
        channel=channel if isinstance(channel, str) else None,
        channel_id=channel_id if isinstance(channel_id, str) else None,
        channel_url=f"https://www.youtube.com/channel/{channel_id}" if channel_id else None,
        description=description if isinstance(description, str) else None,
        tags=tags,
        category=_category_from_snippet(snippet, topic),
        licensed_content=licensed_content,
        published_at=published_at if isinstance(published_at, str) else None,
        view_count=view_count,
        thumbnail_url=_first_thumbnail(snippet),
        raw=None,
    )


def label_from_description(description: Optional[str]) -> Optional[str]:
    """Extract the label from YouTube's ``Provided to YouTube by <label>`` line."""
    if not description:
        return None
    match = _LABEL_RE.search(description)
    if not match:
        return None
    label = match.group("label").strip()
    return label or None


def build_youtube_credits(metadata: TrackMetadata) -> TrackCredits:
    """Best-effort credits from the only signals YouTube exposes publicly."""
    performers: List[Credit] = []
    if metadata.channel:
        performers.append(Credit(name=metadata.channel, role="Uploader"))
    label = label_from_description(metadata.description)
    return TrackCredits(
        available=bool(performers or label),
        source_note=(
            "YouTube Data API exposes the channel/uploader, the licensedContent flag, and "
            "any 'Provided to YouTube by <label>' line. ISRC and songwriter/publisher credits "
            "are not available from YouTube; the AI research step fills those in."
        ),
        performers=performers,
        source_label=label,
    )


class YouTubeSource:
    """High-level entry point: YouTube URL/id/query -> (:class:`TrackMetadata`, :class:`TrackCredits`)."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        # Resolve lazily so constructing the source (e.g. via Pipeline) never
        # requires a key; only an actual YouTube request does.
        self._api_key = api_key
        self._timeout = timeout

    def resolve_video_id(self, url_or_id_or_query: str) -> str:
        """Return a video id, searching YouTube when the input isn't a URL/id."""
        try:
            return parse_video_id(url_or_id_or_query)
        except InvalidYouTubeURLError:
            query = (url_or_id_or_query or "").strip()
            if not query:
                raise
            return self.search_video(query)

    def search_video(self, query: str) -> str:
        """Resolve a free-text query to the best-matching music video id."""
        key = resolve_api_key(self._api_key)
        payload = _request_json(
            "search",
            {
                "part": "snippet",
                "type": "video",
                "maxResults": 1,
                "videoCategoryId": YOUTUBE_MUSIC_CATEGORY_ID,
                "q": query,
                "key": key,
            },
            self._timeout,
        )
        items = payload.get("items")
        if not items:
            # A song may be uploaded outside the Music category; retry broadly.
            payload = _request_json(
                "search",
                {"part": "snippet", "type": "video", "maxResults": 1, "q": query, "key": key},
                self._timeout,
            )
            items = payload.get("items")
        if not items:
            raise YouTubeLookupError(f"No YouTube video found for query {query!r}.")
        video_id = (items[0].get("id") or {}).get("videoId") if isinstance(items[0], dict) else None
        if not video_id:
            raise YouTubeLookupError(f"YouTube search returned no usable video id for {query!r}.")
        return video_id

    def fetch_video(self, video_id: str) -> tuple[TrackMetadata, TrackCredits]:
        """Fetch and normalize everything available for a single video id."""
        payload = _request_json(
            "videos",
            {
                "part": "snippet,contentDetails,statistics,topicDetails",
                "id": video_id,
                "key": resolve_api_key(self._api_key),
            },
            self._timeout,
        )
        items = payload.get("items")
        if not items:
            raise YouTubeLookupError(
                f"YouTube video {video_id!r} was not found, is private, or is unavailable."
            )
        raw = items[0]
        if not isinstance(raw, dict):
            raise YouTubeLookupError(
                f"Unexpected response type from the YouTube Data API for video {video_id!r}: {type(raw)!r}"
            )
        metadata = normalize_video_info(raw, video_id)
        return metadata, build_youtube_credits(metadata)

    def fetch(self, url_or_id_or_query: str) -> tuple[TrackMetadata, TrackCredits]:
        """Resolve the input to a video id (searching if needed), then fetch it."""
        video_id = self.resolve_video_id(url_or_id_or_query)
        return self.fetch_video(video_id)
