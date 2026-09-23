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
from .models import Comment, Credit, TrackCredits, TrackMetadata

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_API_KEY_ENV = "YOUTUBE_API_KEY"
DEFAULT_TIMEOUT_SECONDS = 30.0
YOUTUBE_MUSIC_CATEGORY_ID = "10"
MAX_DESCRIPTION_CHARS = 4000
MAX_COMMENT_CHARS = 600
MAX_COMMENTS = 10
DEFAULT_COMMENT_RESULTS = 20
MAX_STATEMENT_CHARS = 400
MAX_NETWORK_ATTEMPTS = 3
DEFAULT_SEARCH_RESULTS = 5
MAX_SEARCH_RESULTS = 5

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)
_LABEL_RE = re.compile(r"Provided to YouTube by\s+(?P<label>[^\r\n]+)", re.IGNORECASE)
# Phrases that signal a creator/rights-holder usage licence rather than a
# platform mechanism. Kept deliberately broad; the AI corroborates the match.
_LICENSE_STATEMENT_RE = re.compile(
    r"royalt(?:y|ies)[\s-]?free|free\s+to\s+use|free\s+for\s+use|free\s+to\s+(?:stream|monetize|reuse|use)|"
    r"copyright[\s-]?free|no\s+copyright|creative\s+commons|cc[\s-]?by|public\s+domain|"
    r"use\s+in\s+your\s+(?:videos?|streams?|content)|for\s+credited\s+(?:cover|remix)",
    re.IGNORECASE,
)

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


def normalize_search_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Turn one ``search.list`` item into a lightweight candidate for the UI.

    Search results are only used to let a controller pick a video, so this
    keeps the identifying fields plus the thumbnail instead of the full
    :class:`TrackMetadata` shape. Returns ``None`` for items without a usable
    video id (e.g. channel/playlist results).
    """
    if not isinstance(item, dict):
        return None
    raw_id = item.get("id")
    video_id = raw_id.get("videoId") if isinstance(raw_id, dict) else None
    if not isinstance(video_id, str) or not _VIDEO_ID_RE.match(video_id):
        return None
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    return {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": snippet.get("title"),
        "channel": snippet.get("channelTitle"),
        "channel_id": snippet.get("channelId"),
        "description": snippet.get("description"),
        "published_at": snippet.get("publishedAt"),
        "thumbnail_url": _first_thumbnail(snippet),
    }


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


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_comments(
    payload: Dict[str, Any],
    *,
    channel_id: Optional[str] = None,
    max_comments: int = MAX_COMMENTS,
) -> List[Comment]:
    """Turn a ``commentThreads.list`` response into :class:`Comment` objects.

    ``order=relevance`` returns the uploader's pinned comment first, which is
    where usage licences are usually declared. Comments by the uploader's own
    channel are flagged with ``is_uploader``.
    """
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    comments: List[Comment] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        thread = item.get("snippet")
        if not isinstance(thread, dict):
            continue
        top = thread.get("topLevelComment")
        snippet = top.get("snippet") if isinstance(top, dict) else None
        if not isinstance(snippet, dict):
            continue
        author_channel = snippet.get("authorChannelId")
        author_channel_id = author_channel.get("value") if isinstance(author_channel, dict) else None
        text = snippet.get("textDisplay") or snippet.get("textOriginal")
        if isinstance(text, str):
            text = re.sub(r"\s+", " ", text).strip()[:MAX_COMMENT_CHARS] or None
        else:
            text = None
        comments.append(
            Comment(
                author=snippet.get("authorDisplayName"),
                author_channel_id=author_channel_id if isinstance(author_channel_id, str) else None,
                text=text,
                like_count=_int_or_none(snippet.get("likeCount")),
                published_at=snippet.get("publishedAt"),
                is_uploader=bool(channel_id and author_channel_id == channel_id),
            )
        )
        if len(comments) >= max_comments:
            break
    return comments


def license_statements_from_text(text: Optional[str]) -> List[str]:
    """Return lines/sentences that declare a usage licence or free-use term."""
    if not text:
        return []
    statements: List[str] = []
    for raw_line in re.split(r"[\r\n]+", text):
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line and _LICENSE_STATEMENT_RE.search(line):
            statements.append(line[:MAX_STATEMENT_CHARS])
    return statements


def collect_license_statements(
    description: Optional[str],
    comments: Optional[List[Comment]] = None,
) -> List[str]:
    """Gather creator-declared licence statements from the description + comments.

    De-duplicated in order of appearance (description first, then the
    uploader's comments, then other comments) and capped so the AI payload
    stays small.
    """
    statements: List[str] = []
    seen: set = set()

    def add(candidate: str) -> None:
        key = candidate.casefold()
        if key not in seen:
            seen.add(key)
            statements.append(candidate)

    for statement in license_statements_from_text(description):
        add(statement)
    ordered = sorted(comments or [], key=lambda c: not c.is_uploader)
    for comment in ordered:
        for statement in license_statements_from_text(comment.text):
            add(statement)
    return statements[:MAX_COMMENTS]


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

    def search_videos(
        self, query: str, limit: int = DEFAULT_SEARCH_RESULTS
    ) -> List[Dict[str, Any]]:
        """Return up to ``limit`` candidate videos (with thumbnails) for a query.

        Each candidate is a plain dict (``video_id``, ``url``, ``title``,
        ``channel``, ``channel_id``, ``description``, ``published_at``,
        ``thumbnail_url``) so a controller can show them and pick one before
        calling ``fetch_video``/``check_youtube_url``.
        """
        key = resolve_api_key(self._api_key)
        limit = max(1, min(int(limit), MAX_SEARCH_RESULTS))
        payload = _request_json(
            "search",
            {
                "part": "snippet",
                "type": "video",
                "maxResults": limit,
                "videoCategoryId": YOUTUBE_MUSIC_CATEGORY_ID,
                "q": query,
                "key": key,
            },
            self._timeout,
        )
        results = self._search_results(payload)
        if not results:
            # A song may be uploaded outside the Music category; retry broadly.
            payload = _request_json(
                "search",
                {"part": "snippet", "type": "video", "maxResults": limit, "q": query, "key": key},
                self._timeout,
            )
            results = self._search_results(payload)
        return results

    @staticmethod
    def _search_results(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        items = payload.get("items")
        if not isinstance(items, list):
            return []
        return [candidate for candidate in (normalize_search_item(item) for item in items) if candidate]

    def search_video(self, query: str) -> str:
        """Resolve a free-text query to the best-matching music video id."""
        results = self.search_videos(query, limit=1)
        if not results:
            raise YouTubeLookupError(f"No YouTube video found for query {query!r}.")
        return str(results[0]["video_id"])

    def fetch_comments(
        self,
        video_id: str,
        *,
        channel_id: Optional[str] = None,
        limit: int = DEFAULT_COMMENT_RESULTS,
    ) -> List[Comment]:
        """Best-effort fetch of top/pinned comments; never fails a video lookup.

        Comments can be disabled, the video may have none, or the API may error
        (quota, etc.). Any of those just yields ``[]`` so the licensing research
        still proceeds with the metadata we already have.
        """
        try:
            payload = _request_json(
                "commentThreads",
                {
                    "part": "snippet",
                    "videoId": video_id,
                    "order": "relevance",
                    "maxResults": max(1, min(int(limit), 100)),
                    "textFormat": "plainText",
                    "key": resolve_api_key(self._api_key),
                },
                self._timeout,
            )
        except (YouTubeAPIError, YouTubeLookupError):
            return []
        return normalize_comments(payload, channel_id=channel_id)

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
        metadata.top_comments = self.fetch_comments(video_id, channel_id=metadata.channel_id)
        metadata.license_statements = collect_license_statements(
            metadata.description, metadata.top_comments
        )
        return metadata, build_youtube_credits(metadata)

    def fetch(self, url_or_id_or_query: str) -> tuple[TrackMetadata, TrackCredits]:
        """Resolve the input to a video id (searching if needed), then fetch it."""
        video_id = self.resolve_video_id(url_or_id_or_query)
        return self.fetch_video(video_id)
