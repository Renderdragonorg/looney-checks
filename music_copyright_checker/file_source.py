"""Reads name/artist/album/ISRC-style metadata out of a local audio file.

Uses `mutagen <https://mutagen.readthedocs.io/>`_, which handles MP3, FLAC,
M4A/AAC, OGG Vorbis/Opus, WAV, and most other common formats through one
API, with format-specific fallbacks for the handful of tags (ISRC in
particular) that aren't part of mutagen's cross-format "easy" tag set.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .errors import FileMetadataError
from .models import FileMetadata

SUPPORTED_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".wav", ".wma",
}


def _first_tag(easy_tags: Any, *keys: str) -> Optional[str]:
    for key in keys:
        val = easy_tags.get(key) if easy_tags else None
        if val:
            return val[0] if isinstance(val, list) else val
    return None


def _json_safe_tag(value: Any) -> Any:
    """Keep uncommon mutagen tag objects serializable for prompts and cache entries."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, list):
        return [_json_safe_tag(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe_tag(item) for key, item in value.items()}
    return str(value)


def _extract_isrc(path: str, ext: str) -> Optional[str]:
    """ISRC lives in different frames/fields per format; easy-tags don't cover it uniformly."""
    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3
            tags = ID3(path)
            frame = tags.get("TSRC")
            if frame and frame.text:
                return str(frame.text[0])
        elif ext == ".flac":
            from mutagen.flac import FLAC
            tags = FLAC(path)
            for key in ("isrc", "ISRC"):
                if key in tags:
                    return tags[key][0]
        elif ext in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4
            tags = MP4(path)
            for key in ("----:com.apple.iTunes:ISRC", "isrc"):
                if key in tags.tags:
                    val = tags.tags[key][0]
                    return val.decode("utf-8", "ignore") if isinstance(val, bytes) else str(val)
        elif ext in (".ogg", ".oga", ".opus"):
            from mutagen.oggvorbis import OggVorbis
            tags = OggVorbis(path)
            for key in ("isrc", "ISRC"):
                if key in tags:
                    return tags[key][0]
    except Exception:
        return None
    return None


class FileSource:
    """High-level entry point: local audio file path -> :class:`FileMetadata`."""

    def fetch(self, path: str, *, fallback_title: Optional[str] = None) -> FileMetadata:
        if not os.path.isfile(path):
            raise FileMetadataError(f"No such file: {path!r}")

        ext = os.path.splitext(path)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise FileMetadataError(
                f"Unsupported audio file extension {ext!r} for {path!r}. "
                f"Supported: {sorted(SUPPORTED_EXTENSIONS)}"
            )

        try:
            import mutagen
        except ImportError as exc:
            raise FileMetadataError("The 'mutagen' package is required (pip install mutagen).") from exc

        try:
            audio = mutagen.File(path, easy=True)
            raw_audio = mutagen.File(path)  # non-easy, for length/bitrate/comment
        except Exception as exc:
            raise FileMetadataError(f"Failed to read audio metadata from {path!r}: {exc}") from exc

        if audio is None:
            raise FileMetadataError(f"mutagen could not recognize {path!r} as an audio file.")

        easy = audio.tags or {}
        title = _first_tag(easy, "title") or fallback_title
        if not title:
            title = os.path.splitext(os.path.basename(path))[0] or None
        artists_raw = easy.get("artist", [])
        artists: List[str] = list(artists_raw) if isinstance(artists_raw, list) else [artists_raw]
        album = _first_tag(easy, "album")
        album_artist = _first_tag(easy, "albumartist", "album artist")
        date = _first_tag(easy, "date", "originaldate", "year")
        comment = _first_tag(easy, "comment", "description")

        duration = None
        bitrate = None
        info = getattr(raw_audio, "info", None)
        if info is not None:
            duration = getattr(info, "length", None)
            bitrate = getattr(info, "bitrate", None)

        isrc = _extract_isrc(path, ext)

        # Anything else present in the easy tag set that we haven't already
        # surfaced explicitly - handy context for the AI (e.g. label,
        # copyright, catalog number fields some rippers embed).
        known = {"title", "artist", "album", "albumartist", "album artist", "date", "originaldate", "year", "comment", "description"}
        extra_tags: Dict[str, Any] = {
            k: _json_safe_tag(v[0] if isinstance(v, list) and len(v) == 1 else v)
            for k, v in easy.items()
            if k not in known
        }

        return FileMetadata(
            path=path,
            format=ext.lstrip("."),
            title=title,
            artists=[a for a in artists if a],
            album=album,
            album_artist=album_artist,
            date=date,
            isrc=isrc,
            duration_seconds=duration,
            bitrate=bitrate,
            comment=comment,
            extra_tags=extra_tags,
        )
