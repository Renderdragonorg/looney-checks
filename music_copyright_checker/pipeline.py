"""End-to-end orchestration: source -> normalized metadata -> AI research -> result.

This is the package's main entry point. It intentionally has zero UI
dependencies - it's meant to be imported by a Tauri backend (via a Python
sidecar/RPC layer), a CLI, a test suite, or anything else.

Typical usage::

    from music_copyright_checker import Pipeline

    # Default backend is OpenRouter (needs OPENROUTER_API_KEY); pass
    # ai_backend="opencode" to use the opencode CLI agent instead.
    pipeline = Pipeline()

    result = pipeline.check_spotify_url("https://open.spotify.com/track/....")
    print(result.to_dict())

    result_yt = pipeline.check_youtube_url("https://www.youtube.com/watch?v=...")
    print(result_yt.to_dict())

    result2 = pipeline.check_file("/path/to/song.mp3")
    print(result2.to_dict())
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from .ai_researcher import AIResearcher, DEFAULT_OPENCODE_MODEL, DEFAULT_OPENCODE_TIMEOUT
from .exa_search import DEFAULT_EXA_BASE_URL
from .fallback_researcher import FallbackResearcher
from .openrouter_client import (
    DEFAULT_OPENROUTER_BASE_URL,
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_OPENROUTER_TIMEOUT,
)
from .openrouter_researcher import OpenRouterResearcher
from .opencode_go_client import (
    DEFAULT_OPENCODE_GO_BASE_URL,
    DEFAULT_OPENCODE_GO_MODEL,
    DEFAULT_OPENCODE_GO_TIMEOUT,
)
from .opencode_go_researcher import OpenCodeGoResearcher
from .openai_compatible_client import DEFAULT_OPENAI_COMPAT_TIMEOUT
from .openai_compatible_researcher import OpenAICompatibleResearcher
from .cache import (
    DEFAULT_FILE_METADATA_TTL_SECONDS,
    DEFAULT_METADATA_TTL_SECONDS,
    DEFAULT_NOT_FOUND_RESEARCH_TTL_SECONDS,
    DEFAULT_PARTIAL_RESEARCH_TTL_SECONDS,
    DEFAULT_RESEARCH_TTL_SECONDS,
    CacheStore,
    InFlight,
    file_metadata_value,
    hash_file,
    metadata_cache_key,
    research_cache_key,
)
from .errors import InvalidYouTubeURLError, MusicCheckerError, YouTubeLookupError
from .file_source import FileSource
from .models import (
    Credit,
    CopyrightCheckResult,
    LookupRequest,
    TrackCredits,
    TrackMetadata,
    file_metadata_from_dict,
    research_result_from_dict,
    track_credits_from_dict,
    track_metadata_from_dict,
)
from .prompts import RESEARCH_PROMPT_VERSION
from .spotify_source import SpotifySource, parse_track_id
from .youtube_source import YouTubeSource, parse_video_id


def _query_identity(query: str) -> str:
    """Normalize a free-text search query for the query->video-id cache."""
    return re.sub(r"\s+", " ", (query or "").strip().casefold())


class Pipeline:
    """Wires together the Spotify/YouTube/file sources and the AI researcher."""

    def __init__(
        self,
        *,
        # Spotify
        spotify_language: str = "en",
        # YouTube Data API v3
        youtube_api_key: Optional[str] = None,
        # AI backend selection
        ai_backend: str = "openrouter",  # primary backend (see _VALID_AI_BACKENDS)
        ai_fallback_backends: Optional[Sequence[str]] = None,  # tried in order if primary fails
        ai_model: Optional[str] = None,  # override the primary backend's default model
        ai_models: Optional[Mapping[str, str]] = None,  # per-backend model overrides
        # Web search
        web_search_backend: str = "auto",  # "auto" | "server" | "exa" | "none"
        exa_api_key: Optional[str] = None,
        exa_base_url: str = DEFAULT_EXA_BASE_URL,
        exa_timeout: Optional[float] = None,
        # AI / OpenRouter direct REST (default backend)
        openrouter_api_key: Optional[str] = None,
        openrouter_base_url: str = DEFAULT_OPENROUTER_BASE_URL,
        openrouter_web_search: bool = True,
        openrouter_timeout: float = DEFAULT_OPENROUTER_TIMEOUT,
        # AI / OpenCode Go direct REST (optional backend)
        opencode_go_api_key: Optional[str] = None,
        opencode_go_base_url: str = DEFAULT_OPENCODE_GO_BASE_URL,
        opencode_go_web_search: bool = True,
        opencode_go_timeout: float = DEFAULT_OPENCODE_GO_TIMEOUT,
        # AI / generic OpenAI-compatible direct REST (optional backend)
        openai_compatible_api_key: Optional[str] = None,
        openai_compatible_base_url: Optional[str] = None,
        openai_compatible_model: Optional[str] = None,
        openai_compatible_api_key_env: Optional[str] = None,
        openai_compatible_web_search: bool = True,
        openai_compatible_timeout: Optional[float] = None,
        # AI / opencode-harness (optional legacy backend)
        opencode_server: Optional[str] = None,
        opencode_binary: str = "opencode",
        opencode_model: Optional[str] = DEFAULT_OPENCODE_MODEL,
        opencode_auto_approve: bool = True,
        opencode_timeout: float = DEFAULT_OPENCODE_TIMEOUT,
        opencode_username: Optional[str] = None,
        opencode_password: Optional[str] = None,
        opencode_auto_install: bool = False,
        run_ai_research: bool = True,
        cache_enabled: bool = True,
        cache_path: Optional[str] = None,
        cache_store: Optional[CacheStore] = None,
        metadata_ttl_seconds: float = DEFAULT_METADATA_TTL_SECONDS,
        file_metadata_ttl_seconds: float = DEFAULT_FILE_METADATA_TTL_SECONDS,
        research_ttl_seconds: float = DEFAULT_RESEARCH_TTL_SECONDS,
    ) -> None:
        self._spotify = SpotifySource(language=spotify_language)
        self._youtube = YouTubeSource(api_key=youtube_api_key)
        self._file = FileSource()
        self._run_ai_research = run_ai_research
        self._ai_backend = ai_backend
        self._ai_model_override = ai_model
        self._ai_models: Dict[str, str] = {str(k): str(v) for k, v in (ai_models or {}).items()}
        fallback_backends = [str(backend) for backend in (ai_fallback_backends or [])]
        self._ai_backends = [ai_backend, *fallback_backends]
        invalid = [backend for backend in self._ai_backends if backend not in self._VALID_AI_BACKENDS]
        if invalid:
            allowed = ", ".join(sorted(self._VALID_AI_BACKENDS))
            raise ValueError(f"ai_backend/ai_fallback_backends must be one of: {allowed}.")

        # Web search config shared by every REST backend.
        self._web_search_backend = web_search_backend
        self._exa_api_key = exa_api_key
        self._exa_base_url = exa_base_url
        self._exa_timeout = exa_timeout

        # Per-backend connection settings.
        self._openrouter_api_key = openrouter_api_key
        self._openrouter_base_url = openrouter_base_url
        self._openrouter_web_search = openrouter_web_search
        self._openrouter_timeout = openrouter_timeout
        self._opencode_go_api_key = opencode_go_api_key
        self._opencode_go_base_url = opencode_go_base_url
        self._opencode_go_web_search = opencode_go_web_search
        self._opencode_go_timeout = opencode_go_timeout
        self._openai_compatible_api_key = openai_compatible_api_key
        self._openai_compatible_base_url = openai_compatible_base_url
        self._openai_compatible_model = openai_compatible_model
        self._openai_compatible_api_key_env = openai_compatible_api_key_env
        self._openai_compatible_web_search = openai_compatible_web_search
        self._openai_compatible_timeout = (
            openai_compatible_timeout if openai_compatible_timeout is not None else DEFAULT_OPENAI_COMPAT_TIMEOUT
        )
        self._opencode_server = opencode_server
        self._opencode_binary = opencode_binary
        self._opencode_model = opencode_model
        self._opencode_auto_approve = opencode_auto_approve
        self._opencode_timeout = opencode_timeout
        self._opencode_username = opencode_username
        self._opencode_password = opencode_password
        self._opencode_auto_install = opencode_auto_install

        self._ai_model = self._model_for(ai_backend) or self._default_model_for(ai_backend)
        # Cache identity covers the whole chain so a fallback result is never
        # served as if the primary backend produced it.
        self._ai_cache_identity = "|".join(
            f"{backend}:{self._model_for(backend) or self._default_model_for(backend)}"
            for backend in self._ai_backends
        )

        self._metadata_ttl_seconds = metadata_ttl_seconds
        self._file_metadata_ttl_seconds = file_metadata_ttl_seconds
        self._research_ttl_seconds = research_ttl_seconds
        self._cache = (cache_store or CacheStore(cache_path)) if cache_enabled else None
        self._in_flight = InFlight()
        self._ai: Optional[Any] = None
        if run_ai_research:
            researchers = [self._build_researcher(backend) for backend in self._ai_backends]
            self._ai = researchers[0] if len(researchers) == 1 else FallbackResearcher(researchers)

    # -- AI backend construction ---------------------------------------

    _VALID_AI_BACKENDS = {"openrouter", "opencode-go", "openai-compatible", "opencode"}

    def _default_model_for(self, backend: str) -> str:
        if backend == "openrouter":
            return DEFAULT_OPENROUTER_MODEL
        if backend == "opencode-go":
            return DEFAULT_OPENCODE_GO_MODEL
        if backend == "openai-compatible":
            return self._openai_compatible_model or os.environ.get("OPENAI_COMPAT_MODEL") or ""
        return self._opencode_model or DEFAULT_OPENCODE_MODEL

    def _model_for(self, backend: str) -> str:
        if backend == self._ai_backend and self._ai_model_override:
            return self._ai_model_override
        override = self._ai_models.get(backend)
        if override:
            return override
        return self._default_model_for(backend)

    def _build_researcher(self, backend: str) -> Any:
        model = self._model_for(backend)
        shared = {
            "search_backend": self._web_search_backend,
            "exa_api_key": self._exa_api_key,
            "exa_base_url": self._exa_base_url,
            "exa_timeout": self._exa_timeout,
        }
        if backend == "openrouter":
            return OpenRouterResearcher(
                api_key=self._openrouter_api_key,
                base_url=self._openrouter_base_url,
                model=model or DEFAULT_OPENROUTER_MODEL,
                timeout=self._openrouter_timeout,
                web_search=self._openrouter_web_search,
                **shared,
            )
        if backend == "opencode-go":
            return OpenCodeGoResearcher(
                api_key=self._opencode_go_api_key,
                base_url=self._opencode_go_base_url,
                model=model or DEFAULT_OPENCODE_GO_MODEL,
                timeout=self._opencode_go_timeout,
                web_search=self._opencode_go_web_search,
                **shared,
            )
        if backend == "openai-compatible":
            return OpenAICompatibleResearcher(
                api_key=self._openai_compatible_api_key,
                base_url=self._openai_compatible_base_url,
                model=model or None,
                timeout=self._openai_compatible_timeout,
                api_key_env=self._openai_compatible_api_key_env,
                web_search=self._openai_compatible_web_search,
                **shared,
            )
        if self._opencode_auto_install and self._opencode_server is None:
            from .bootstrap import ensure_opencode

            self._opencode_binary = ensure_opencode(self._opencode_binary, auto_install=True)
        return AIResearcher(
            server=self._opencode_server,
            binary=self._opencode_binary,
            model=model or DEFAULT_OPENCODE_MODEL,
            auto_approve=self._opencode_auto_approve,
            timeout=self._opencode_timeout,
            username=self._opencode_username,
            password=self._opencode_password,
        )

    # -- public API ---------------------------------------------------

    def check_spotify_url(
        self,
        url_or_uri: str,
        *,
        progress: Optional[Callable[[str, str], None]] = None,
        refresh: bool = False,
    ) -> CopyrightCheckResult:
        """Look up a Spotify track URL/URI, then run AI licensing research on it."""
        track_id = parse_track_id(url_or_uri)
        metadata_cache_hit = False
        track: TrackMetadata
        credits_: TrackCredits
        cache_key = metadata_cache_key("spotify", track_id)
        if progress:
            progress("identifying_track", "Fetching Spotify metadata and credits.")
        cached = None if refresh or self._cache is None else self._cache.get(cache_key)
        if cached is not None:
            try:
                track = track_metadata_from_dict(cached.value["track"])
                credits_ = track_credits_from_dict(cached.value["credits"])
                metadata_cache_hit = True
            except (AttributeError, KeyError, TypeError, ValueError):
                cached = None
        if cached is None:
            track, credits_ = self._spotify.fetch(url_or_uri)
            if self._cache is not None:
                self._cache.set(
                    cache_key,
                    {"track": track.to_dict(), "credits": credits_.to_dict()},
                    self._metadata_ttl_seconds,
                )
        request = LookupRequest(
            source="spotify",
            input_ref=url_or_uri,
            track=track,
            credits=credits_,
        )
        return self._run(
            request,
            progress=progress,
            refresh=refresh,
            metadata_cache_hit=metadata_cache_hit,
        )

    def check_youtube_url(
        self,
        url_or_query: str,
        *,
        progress: Optional[Callable[[str, str], None]] = None,
        refresh: bool = False,
    ) -> CopyrightCheckResult:
        """Look up a YouTube video (URL, id, or search query), then run AI licensing research.

        Mirrors :meth:`check_spotify_url`: metadata/credits are cached by video
        id, then the normalized request is handed to the AI researcher. A
        free-text query is resolved through ``search.list`` first, and that
        query -> video-id mapping is cached to conserve API quota.
        """
        if progress:
            progress("identifying_track", "Resolving the YouTube video and fetching metadata.")
        video_id = self._resolve_youtube_video_id(url_or_query, refresh=refresh)
        metadata_cache_hit = False
        track: TrackMetadata
        credits_: TrackCredits
        cache_key = metadata_cache_key("youtube", video_id)
        cached = None if refresh or self._cache is None else self._cache.get(cache_key)
        if cached is not None:
            try:
                track = track_metadata_from_dict(cached.value["track"])
                credits_ = track_credits_from_dict(cached.value["credits"])
                metadata_cache_hit = True
            except (AttributeError, KeyError, TypeError, ValueError):
                cached = None
        if cached is None:
            track, credits_ = self._youtube.fetch_video(video_id)
            if self._cache is not None:
                self._cache.set(
                    cache_key,
                    {"track": track.to_dict(), "credits": credits_.to_dict()},
                    self._metadata_ttl_seconds,
                )
        request = LookupRequest(
            source="youtube",
            input_ref=url_or_query,
            track=track,
            credits=credits_,
        )
        return self._run(
            request,
            progress=progress,
            refresh=refresh,
            metadata_cache_hit=metadata_cache_hit,
        )

    def search_youtube(self, query: str, *, limit: int = 5) -> list[Dict[str, Any]]:
        """Search YouTube for candidates (with thumbnails) instead of auto-picking.

        Lets a controller show the top matches and choose one; pass the chosen
        ``video_id``/``url`` to :meth:`check_youtube_url` to run the actual
        copyright check. Free-text queries to :meth:`check_youtube_url` still
        auto-select the best match.
        """
        query = (query or "").strip()
        if not query:
            raise YouTubeLookupError("A non-empty search query is required.")
        return self._youtube.search_videos(query, limit=limit)

    def _resolve_youtube_video_id(self, url_or_query: str, *, refresh: bool = False) -> str:
        try:
            return parse_video_id(url_or_query)
        except InvalidYouTubeURLError:
            pass
        query_key = metadata_cache_key("youtube-query", _query_identity(url_or_query))
        if not refresh and self._cache is not None:
            cached = self._cache.get(query_key)
            if cached is not None and cached.value.get("video_id"):
                return str(cached.value["video_id"])
        video_id = self._youtube.search_video(url_or_query)
        if self._cache is not None:
            self._cache.set(query_key, {"video_id": video_id}, self._metadata_ttl_seconds)
        return video_id

    def check_file(
        self,
        path: str,
        *,
        progress: Optional[Callable[[str, str], None]] = None,
        fallback_title: Optional[str] = None,
        refresh: bool = False,
    ) -> CopyrightCheckResult:
        """Read tags off a local audio file, then run AI licensing research on it."""
        if progress:
            progress("extracting_metadata", "Reading audio tags and technical metadata.")
        file_hash = None
        if os.path.isfile(path):
            try:
                file_hash = hash_file(path)
            except OSError:
                # Let FileSource produce the normal user-facing metadata error.
                file_hash = None
        metadata_cache_hit = False
        file_cache_key = metadata_cache_key("file-sha256", file_hash) if file_hash else None
        cached = (
            None
            if refresh or self._cache is None or file_cache_key is None
            else self._cache.get(file_cache_key)
        )
        if cached is not None:
            try:
                file_meta = file_metadata_from_dict(cached.value, path=path)
                metadata_cache_hit = True
            except (AttributeError, TypeError, ValueError):
                cached = None
        if cached is None:
            file_meta = self._file.fetch(path, fallback_title=fallback_title)
            if self._cache is not None and file_cache_key is not None:
                self._cache.set(
                    file_cache_key,
                    file_metadata_value(file_meta),
                    self._file_metadata_ttl_seconds,
                )
        track = TrackMetadata(
            name=file_meta.title,
            artists=file_meta.artists,
            album=file_meta.album,
            album_artists=[file_meta.album_artist] if file_meta.album_artist else [],
            release_date=file_meta.date,
            isrc=file_meta.isrc,
            duration_ms=int(file_meta.duration_seconds * 1000) if file_meta.duration_seconds else None,
        )
        credits_ = TrackCredits(
            available=bool(file_meta.artists),
            source_note=(
                "derived from local file tags only" if file_meta.artists else "no artist tag present in file"
            ),
            performers=[Credit(name=a, role="Performer") for a in file_meta.artists],
        )
        request = LookupRequest(
            source="file",
            input_ref=path,
            track=track,
            credits=credits_,
            file_metadata=file_meta,
        )
        return self._run(
            request,
            progress=progress,
            refresh=refresh,
            metadata_cache_hit=metadata_cache_hit,
            fallback_identity=f"file-sha256:{file_hash}" if file_hash else None,
        )

    def build_request_json(self, request: LookupRequest) -> Dict[str, Any]:
        """Expose the normalized JSON payload without running AI research (for UI previews)."""
        return request.to_dict()

    # -- internals ------------------------------------------------------

    def _run(
        self,
        request: LookupRequest,
        *,
        progress: Optional[Callable[[str, str], None]] = None,
        refresh: bool = False,
        metadata_cache_hit: bool = False,
        fallback_identity: Optional[str] = None,
    ) -> CopyrightCheckResult:
        if not self._run_ai_research or self._ai is None:
            from .models import ResearchResult

            result = CopyrightCheckResult(
                request=request,
                research=ResearchResult(summary="AI research step was disabled for this run."),
                ai_meta={"metadata_cache_hit": metadata_cache_hit},
            )
            if progress:
                progress("complete", "Metadata extraction finished; AI research was disabled.")
            return result

        cache_key = research_cache_key(
            request,
            model=getattr(self, "_ai_cache_identity", None) or self._ai_model,
            prompt_version=RESEARCH_PROMPT_VERSION,
            fallback_identity=fallback_identity,
        )
        cached = None if refresh or self._cache is None else self._cache.get(cache_key)
        if cached is not None:
            try:
                research_result = research_result_from_dict(cached.value["research"])
                ai_meta = dict(cached.value.get("ai_meta") or {})
                ai_meta["cost"] = 0.0
                ai_meta["tokens"] = {}
                ai_meta["cache_hit"] = True
                ai_meta["cache_age_seconds"] = round(cached.age_seconds, 3)
                ai_meta["cached_at"] = cached.created_at
            except (AttributeError, KeyError, TypeError, ValueError):
                cached = None
        if cached is None:
            if progress:
                progress("researching", "Searching rights, licensing, and usage sources with AI.")
            lock = self._in_flight.lock_for(cache_key)
            with lock:
                cached = None if refresh or self._cache is None else self._cache.get(cache_key)
                if cached is not None:
                    try:
                        research_result = research_result_from_dict(cached.value["research"])
                        ai_meta = dict(cached.value.get("ai_meta") or {})
                        ai_meta["cost"] = 0.0
                        ai_meta["tokens"] = {}
                        ai_meta["cache_hit"] = True
                        ai_meta["cache_age_seconds"] = round(cached.age_seconds, 3)
                        ai_meta["cached_at"] = cached.created_at
                    except (AttributeError, KeyError, TypeError, ValueError):
                        cached = None
                if cached is None:
                    research_result, ai_meta = self._ai.research(request)
                    ai_meta = dict(ai_meta)
                    ai_meta["cache_hit"] = False
                    if self._cache is not None:
                        research_ttl = self._research_ttl_seconds
                        if research_result.status == "partial":
                            research_ttl = min(
                                research_ttl, DEFAULT_PARTIAL_RESEARCH_TTL_SECONDS
                            )
                        elif research_result.status == "not_found":
                            research_ttl = min(
                                research_ttl, DEFAULT_NOT_FOUND_RESEARCH_TTL_SECONDS
                            )
                        self._cache.set(
                            cache_key,
                            {
                                "research": research_result.to_dict(),
                                "ai_meta": ai_meta,
                            },
                            research_ttl,
                        )
        ai_meta["metadata_cache_hit"] = metadata_cache_hit
        result = CopyrightCheckResult(request=request, research=research_result, ai_meta=ai_meta)
        if progress:
            progress("complete", "Copyright and licensing research finished.")
        return result
