"""Exception hierarchy for the music copyright checker package."""

from __future__ import annotations


class MusicCheckerError(Exception):
    """Base class for every error raised by this package."""


class InvalidSpotifyURLError(MusicCheckerError):
    """Raised when a Spotify URL/URI could not be parsed into a track id."""


class SpotifyLookupError(MusicCheckerError):
    """Raised when SpotAPI fails to return track data for a track id."""


class InvalidYouTubeURLError(MusicCheckerError):
    """Raised when a value could not be parsed as a YouTube video id/URL."""


class YouTubeLookupError(MusicCheckerError):
    """Raised when the YouTube Data API fails to return video data."""


class YouTubeAPIError(YouTubeLookupError):
    """Raised when the YouTube Data API returns an explicit error response."""


class FileMetadataError(MusicCheckerError):
    """Raised when a local audio file's metadata cannot be read."""


class AIResearchError(MusicCheckerError):
    """Raised when the AI research step (opencode-harness or OpenRouter) fails outright."""


class OpenRouterError(AIResearchError):
    """Raised when the direct OpenRouter API research step fails.

    Subclasses :class:`AIResearchError` so callers that already handle the AI
    research step keep working unchanged.
    """


class OpenCodeGoError(AIResearchError):
    """Raised when the direct OpenCode Go API research step fails.

    Subclasses :class:`AIResearchError` so callers that already handle the AI
    research step keep working unchanged.
    """


class AIResponseParseError(AIResearchError):
    """Raised when the AI's reply could not be parsed as the expected JSON shape."""


class OpenCodeNotInstalledError(MusicCheckerError):
    """Raised when the opencode CLI is required but missing and auto-install is off."""
