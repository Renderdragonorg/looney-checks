"""music_copyright_checker

Underlying, UI-free package for looking up a track (by Spotify URL or local
audio file), collecting whatever metadata/credits are available, and handing
that off to an AI agent (via opencode-harness) to research official
licensing information across the web.

Meant to be consumed by a separate UI layer (e.g. a Tauri app) that calls
into :class:`Pipeline` and serializes the result to JSON for its frontend.
"""

from .errors import (
    AIResearchError,
    AIResponseParseError,
    FileMetadataError,
    InvalidSpotifyURLError,
    MusicCheckerError,
    SpotifyLookupError,
)
from .cache import CacheStore
from .models import (
    Credit,
    CopyrightCheckResult,
    FileMetadata,
    LicenseMatch,
    LookupRequest,
    ResearchResult,
    ResearchSource,
    TrackCredits,
    TrackMetadata,
    UsageAssessment,
)
from .pipeline import Pipeline

__all__ = [
    "Pipeline",
    "CacheStore",
    "Credit",
    "CopyrightCheckResult",
    "FileMetadata",
    "LicenseMatch",
    "LookupRequest",
    "ResearchResult",
    "ResearchSource",
    "UsageAssessment",
    "TrackCredits",
    "TrackMetadata",
    "MusicCheckerError",
    "InvalidSpotifyURLError",
    "SpotifyLookupError",
    "FileMetadataError",
    "AIResearchError",
    "AIResponseParseError",
]

__version__ = "0.1.0"
