"""Persistent cache primitives for metadata and AI research results."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .models import LookupRequest

DEFAULT_CACHE_PATH = os.path.expanduser(
    "~/.cache/music-copyright-checker/cache.sqlite3"
)
DEFAULT_METADATA_TTL_SECONDS = 12 * 60 * 60
DEFAULT_FILE_METADATA_TTL_SECONDS = 30 * 24 * 60 * 60
DEFAULT_RESEARCH_TTL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_PARTIAL_RESEARCH_TTL_SECONDS = 24 * 60 * 60
DEFAULT_NOT_FOUND_RESEARCH_TTL_SECONDS = 6 * 60 * 60


@dataclass(frozen=True)
class CacheRecord:
    value: Dict[str, Any]
    created_at: float
    expires_at: float

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.created_at)


class CacheStore:
    """Small SQLite JSON cache shared by all pipeline callers in a process."""

    def __init__(self, path: Optional[str] = None) -> None:
        configured_path = path or os.environ.get("MUSIC_CHECKER_CACHE_PATH") or DEFAULT_CACHE_PATH
        self.path = os.path.expanduser(configured_path)
        self._write_lock = threading.RLock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    cache_key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS cache_entries_expiry_idx "
                "ON cache_entries(expires_at)"
            )

    def get(self, cache_key: str) -> Optional[CacheRecord]:
        now = time.time()
        with self._write_lock:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT value_json, created_at, expires_at "
                    "FROM cache_entries WHERE cache_key = ?",
                    (cache_key,),
                ).fetchone()
                if row is None:
                    return None
                if row["expires_at"] <= now:
                    connection.execute(
                        "DELETE FROM cache_entries WHERE cache_key = ?", (cache_key,)
                    )
                    return None
                try:
                    value = json.loads(row["value_json"])
                except (TypeError, json.JSONDecodeError):
                    connection.execute(
                        "DELETE FROM cache_entries WHERE cache_key = ?", (cache_key,)
                    )
                    return None
                if not isinstance(value, dict):
                    return None
                return CacheRecord(
                    value=value,
                    created_at=float(row["created_at"]),
                    expires_at=float(row["expires_at"]),
                )

    def set(self, cache_key: str, value: Dict[str, Any], ttl_seconds: float) -> None:
        now = time.time()
        expires_at = now + max(0.0, ttl_seconds)
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO cache_entries(cache_key, value_json, created_at, expires_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        value_json = excluded.value_json,
                        created_at = excluded.created_at,
                        expires_at = excluded.expires_at
                    """,
                    (cache_key, encoded, now, expires_at),
                )
                connection.execute(
                    "DELETE FROM cache_entries WHERE expires_at <= ?", (now,)
                )

    def clear(self) -> None:
        with self._write_lock:
            with self._connect() as connection:
                connection.execute("DELETE FROM cache_entries")


class InFlight:
    """A per-key lock registry used to prevent duplicate expensive AI calls."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: Dict[str, threading.Lock] = {}

    def lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock


def hash_file(path: str) -> str:
    """Return a streaming SHA-256 without retaining audio bytes in memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as audio_file:
        while True:
            chunk = audio_file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def metadata_cache_key(kind: str, identity: str) -> str:
    return f"metadata:v1:{kind}:{identity}"


def _normalized(value: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().casefold())


def research_identity(request: LookupRequest, *, fallback: Optional[str] = None) -> str:
    """Prefer stable recording identifiers, with a conservative metadata fallback."""
    track = request.track
    if track.isrc:
        return f"isrc:{_normalized(track.isrc)}"
    if track.spotify_id:
        return f"spotify:{_normalized(track.spotify_id)}"
    if fallback:
        return fallback
    payload = {
        "name": _normalized(track.name),
        "artists": sorted(_normalized(artist) for artist in track.artists),
        "album": _normalized(track.album),
        "duration_ms": track.duration_ms,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"metadata:{hashlib.sha256(encoded).hexdigest()}"


def research_cache_key(
    request: LookupRequest,
    *,
    model: Optional[str],
    prompt_version: str,
    fallback_identity: Optional[str] = None,
) -> str:
    """Build a key that excludes request paths but includes AI-relevant input."""
    payload = request.to_dict()
    payload.pop("input", None)
    file_metadata = payload.get("file_metadata")
    if isinstance(file_metadata, dict):
        file_metadata.pop("path", None)
    payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    identity = research_identity(request, fallback=fallback_identity)
    model_name = model or "default"
    return f"research:v1:{identity}:{prompt_version}:{model_name}:{payload_hash}"


def file_metadata_value(file_metadata: Any) -> Dict[str, Any]:
    """Serialize file metadata without retaining a local path in the cache."""
    value = file_metadata.to_dict()
    value.pop("path", None)
    return value
