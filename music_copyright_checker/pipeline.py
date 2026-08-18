"""End-to-end orchestration: source -> normalized metadata -> AI research -> result.

This is the package's main entry point. It intentionally has zero UI
dependencies - it's meant to be imported by a Tauri backend (via a Python
sidecar/RPC layer), a CLI, a test suite, or anything else.

Typical usage::

    from music_copyright_checker import Pipeline

    pipeline = Pipeline(opencode_model="sonnet")

    result = pipeline.check_spotify_url("https://open.spotify.com/track/....")
    print(result.to_dict())

    result2 = pipeline.check_file("/path/to/song.mp3")
    print(result2.to_dict())
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional

from .ai_researcher import AIResearcher, DEFAULT_OPENCODE_MODEL, DEFAULT_OPENCODE_TIMEOUT
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
from .errors import MusicCheckerError
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


class Pipeline:
    """Wires together the Spotify/file sources and the AI researcher."""

    def __init__(
        self,
        *,
        # Spotify
        spotify_language: str = "en",
        # AI / opencode-harness
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
        self._file = FileSource()
        self._run_ai_research = run_ai_research
        self._opencode_model = opencode_model
        self._metadata_ttl_seconds = metadata_ttl_seconds
        self._file_metadata_ttl_seconds = file_metadata_ttl_seconds
        self._research_ttl_seconds = research_ttl_seconds
        self._cache = (cache_store or CacheStore(cache_path)) if cache_enabled else None
        self._in_flight = InFlight()
        self._ai: Optional[AIResearcher] = None
        if run_ai_research:
            if opencode_auto_install and opencode_server is None:
                from .bootstrap import ensure_opencode

                opencode_binary = ensure_opencode(opencode_binary, auto_install=True)
            self._ai = AIResearcher(
                server=opencode_server,
                binary=opencode_binary,
                model=opencode_model,
                auto_approve=opencode_auto_approve,
                timeout=opencode_timeout,
                username=opencode_username,
                password=opencode_password,
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
            model=self._opencode_model,
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
