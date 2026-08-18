"""Typed data models shared across the package.

Everything here is a plain :mod:`dataclasses` dataclass with a ``to_dict()``
helper so the objects can be dropped straight into ``json.dumps`` (used both
for the payload we hand to the AI researcher, and for the final result we
hand back to whatever UI sits on top of this package).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _asdict(obj: Any) -> Any:
    """dataclasses.asdict, but tolerant of plain dicts/lists/None nested in."""
    if dataclasses.is_dataclass(obj):
        return {k: _asdict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_asdict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _asdict(v) for k, v in obj.items()}
    return obj


@dataclass
class Credit:
    """A single named contributor credit (performer, writer, producer, ...)."""

    name: str
    role: str
    spotify_uri: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return _asdict(self)


@dataclass
class TrackCredits:
    """All credit information we could gather for a track."""

    available: bool = False
    source_note: str = "not fetched"
    performers: List[Credit] = field(default_factory=list)
    songwriters: List[Credit] = field(default_factory=list)
    producers: List[Credit] = field(default_factory=list)
    source_label: Optional[str] = None  # e.g. "Epic", "Motown" - the record label shown on the credits panel

    def to_dict(self) -> Dict[str, Any]:
        return _asdict(self)


@dataclass
class TrackMetadata:
    """Normalized track info, regardless of whether it came from Spotify or a local file."""

    name: Optional[str] = None
    artists: List[str] = field(default_factory=list)
    album: Optional[str] = None
    album_artists: List[str] = field(default_factory=list)
    release_date: Optional[str] = None
    duration_ms: Optional[int] = None
    isrc: Optional[str] = None
    label: Optional[str] = None
    explicit: Optional[bool] = None
    spotify_id: Optional[str] = None
    spotify_url: Optional[str] = None
    external_ids: Dict[str, str] = field(default_factory=dict)
    raw: Optional[Dict[str, Any]] = None  # optional source payload for debugging / re-parsing

    def to_dict(self) -> Dict[str, Any]:
        return _asdict(self)


@dataclass
class FileMetadata:
    """Tags pulled directly out of a local audio file."""

    path: str
    format: Optional[str] = None
    title: Optional[str] = None
    artists: List[str] = field(default_factory=list)
    album: Optional[str] = None
    album_artist: Optional[str] = None
    date: Optional[str] = None
    isrc: Optional[str] = None
    duration_seconds: Optional[float] = None
    bitrate: Optional[int] = None
    comment: Optional[str] = None
    extra_tags: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _asdict(self)


@dataclass
class LookupRequest:
    """The full, normalized payload that gets serialized and handed to the AI."""

    source: str  # "spotify" | "file"
    input_ref: str  # the original URL/URI or file path
    track: TrackMetadata
    credits: TrackCredits
    file_metadata: Optional[FileMetadata] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "source": self.source,
            "input": self.input_ref,
            "track": self.track.to_dict(),
            "credits": self.credits.to_dict(),
        }
        if self.file_metadata is not None:
            d["file_metadata"] = self.file_metadata.to_dict()
        return d


@dataclass
class LicenseMatch:
    """One candidate licensing/rights-holder record the AI found while researching."""

    source_name: str
    source_url: Optional[str] = None
    confidence: str = "low"  # "high" | "medium" | "low"
    rights_holder: Optional[str] = None
    publisher: Optional[str] = None
    label: Optional[str] = None
    license_type: Optional[str] = None
    territory: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return _asdict(self)


@dataclass
class ResearchSource:
    """A source URL and the claim it supports."""

    name: str
    url: str
    source_type: str = "other"
    supports: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return _asdict(self)


@dataclass
class UsageAssessment:
    """Practical clearance guidance, not a legal opinion."""

    video_verdict: str = "unknown"
    social_media_verdict: str = "unknown"
    reality_tv_verdict: str = "unknown"
    sync_license_required: Optional[bool] = None
    master_license_required: Optional[bool] = None
    platform_exception: Optional[str] = None
    caveats: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _asdict(self)


@dataclass
class ResearchResult:
    """The AI researcher's structured findings."""

    status: str = "not_found"  # "complete" | "partial" | "not_found"
    summary: str = ""
    matches: List[LicenseMatch] = field(default_factory=list)
    sources: List[ResearchSource] = field(default_factory=list)
    usage_assessment: UsageAssessment = field(default_factory=UsageAssessment)
    official_licensing_contacts: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _asdict(self)


@dataclass
class CopyrightCheckResult:
    """Final, top-level object returned by the pipeline."""

    request: LookupRequest
    research: ResearchResult
    ai_meta: Dict[str, Any] = field(default_factory=dict)  # model, cost, tokens, session id...

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "research": self.research.to_dict(),
            "ai_meta": self.ai_meta,
        }


def track_metadata_from_dict(data: Dict[str, Any]) -> TrackMetadata:
    """Restore normalized track metadata stored by the persistent cache."""
    return TrackMetadata(
        name=data.get("name"),
        artists=list(data.get("artists") or []),
        album=data.get("album"),
        album_artists=list(data.get("album_artists") or []),
        release_date=data.get("release_date"),
        duration_ms=data.get("duration_ms"),
        isrc=data.get("isrc"),
        label=data.get("label"),
        explicit=data.get("explicit"),
        spotify_id=data.get("spotify_id"),
        spotify_url=data.get("spotify_url"),
        external_ids=dict(data.get("external_ids") or {}),
        raw=None,
    )


def track_credits_from_dict(data: Dict[str, Any]) -> TrackCredits:
    def credits(key: str) -> List[Credit]:
        return [Credit(**item) for item in (data.get(key) or []) if isinstance(item, dict)]

    return TrackCredits(
        available=bool(data.get("available")),
        source_note=data.get("source_note") or "not fetched",
        performers=credits("performers"),
        songwriters=credits("songwriters"),
        producers=credits("producers"),
        source_label=data.get("source_label"),
    )


def file_metadata_from_dict(data: Dict[str, Any], *, path: str) -> FileMetadata:
    return FileMetadata(
        path=path,
        format=data.get("format"),
        title=data.get("title"),
        artists=list(data.get("artists") or []),
        album=data.get("album"),
        album_artist=data.get("album_artist"),
        date=data.get("date"),
        isrc=data.get("isrc"),
        duration_seconds=data.get("duration_seconds"),
        bitrate=data.get("bitrate"),
        comment=data.get("comment"),
        extra_tags=dict(data.get("extra_tags") or {}),
    )


def research_result_from_dict(data: Dict[str, Any]) -> ResearchResult:
    matches = [
        LicenseMatch(**item)
        for item in (data.get("matches") or [])
        if isinstance(item, dict)
    ]
    sources = [
        ResearchSource(**item)
        for item in (data.get("sources") or [])
        if isinstance(item, dict)
    ]
    usage_data = data.get("usage_assessment") or {}
    usage = UsageAssessment(
        video_verdict=usage_data.get("video_verdict", "unknown"),
        social_media_verdict=usage_data.get("social_media_verdict", "unknown"),
        reality_tv_verdict=usage_data.get("reality_tv_verdict", "unknown"),
        sync_license_required=usage_data.get("sync_license_required"),
        master_license_required=usage_data.get("master_license_required"),
        platform_exception=usage_data.get("platform_exception"),
        caveats=list(usage_data.get("caveats") or []),
    )
    return ResearchResult(
        status=data.get("status", "not_found"),
        summary=data.get("summary", ""),
        matches=matches,
        sources=sources,
        usage_assessment=usage,
        official_licensing_contacts=list(data.get("official_licensing_contacts") or []),
        warnings=list(data.get("warnings") or []),
    )
