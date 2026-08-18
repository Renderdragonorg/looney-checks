"""Exception hierarchy for the music copyright checker package."""

from __future__ import annotations


class MusicCheckerError(Exception):
    """Base class for every error raised by this package."""


class InvalidSpotifyURLError(MusicCheckerError):
    """Raised when a Spotify URL/URI could not be parsed into a track id."""


class SpotifyLookupError(MusicCheckerError):
    """Raised when SpotAPI fails to return track data for a track id."""


class FileMetadataError(MusicCheckerError):
    """Raised when a local audio file's metadata cannot be read."""


class AIResearchError(MusicCheckerError):
    """Raised when the AI research step (opencode-harness) fails outright."""


class AIResponseParseError(AIResearchError):
    """Raised when the AI's reply could not be parsed as the expected JSON shape."""


class OpenCodeNotInstalledError(MusicCheckerError):
    """Raised when the opencode CLI is required but missing and auto-install is off."""
