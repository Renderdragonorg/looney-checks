"""music_copyright_checker

Underlying, UI-free package for looking up a track (by Spotify URL, YouTube
URL/id/query, or local audio file), collecting whatever metadata/credits are
available, and handing that off to an AI researcher (OpenRouter's API by
default, or the optional opencode-harness agent) to research official
licensing information across the web.

Meant to be consumed by a separate UI layer (e.g. a Tauri app) that calls
into :class:`Pipeline` and serializes the result to JSON for its frontend.
"""

from .env import load_env_file

# Load OPENROUTER_API_KEY / YOUTUBE_API_KEY (etc.) from a local .env if present,
# without overriding variables already set in the real environment.
load_env_file()

from .errors import (
    AIResearchError,
    AIResponseParseError,
    AllBackendsFailedError,
    ExaSearchError,
    FileMetadataError,
    InvalidSpotifyURLError,
    InvalidYouTubeURLError,
    MusicCheckerError,
    OpenCodeGoError,
    OpenAICompatibleError,
    OpenRouterError,
    SpotifyLookupError,
    YouTubeAPIError,
    YouTubeLookupError,
)
from .exa_search import ExaSearchClient
from .fallback_researcher import FallbackResearcher
from .openrouter_client import OpenRouterClient
from .openrouter_researcher import OpenRouterResearcher
from .opencode_go_client import OpenCodeGoClient
from .opencode_go_researcher import OpenCodeGoResearcher
from .openai_compatible_client import OpenAICompatibleClient
from .openai_compatible_researcher import OpenAICompatibleResearcher
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
from .youtube_source import YouTubeSource, parse_video_id

__all__ = [
    "Pipeline",
    "YouTubeSource",
    "parse_video_id",
    "OpenRouterClient",
    "OpenRouterResearcher",
    "OpenCodeGoClient",
    "OpenCodeGoResearcher",
    "OpenAICompatibleClient",
    "OpenAICompatibleResearcher",
    "ExaSearchClient",
    "FallbackResearcher",
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
    "InvalidYouTubeURLError",
    "YouTubeLookupError",
    "YouTubeAPIError",
    "FileMetadataError",
    "AIResearchError",
    "AIResponseParseError",
    "OpenRouterError",
    "OpenCodeGoError",
    "OpenAICompatibleError",
    "ExaSearchError",
    "AllBackendsFailedError",
]

__version__ = "0.3.5"
