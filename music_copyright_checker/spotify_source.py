"""Fetches track metadata (and, best-effort, credits) from Spotify via SpotAPI.

SpotAPI (https://github.com/Aran404/SpotAPI) is an unofficial, browser-emulating
wrapper around Spotify's private web endpoints. We only use its public,
no-login surface here (``spotapi.Song.get_track_info``), which is enough for
name/artist/album/ISRC-level metadata.

Spotify does not expose songwriter/producer credits through any endpoint
SpotAPI wraps today - that data only exists behind the "Show Credits" panel
on open.spotify.com, served from an internal, undocumented, frequently-moved
endpoint. Rather than hard-code a private URL that will silently rot, we try
a best-effort fetch (see :func:`_try_fetch_public_credits`) and always fall
back cleanly to "unavailable" - the AI research step is expected to fill in
songwriter/publisher data from public sources (MusicBrainz, ASCAP/BMI/PRS,
label/publisher sites, etc.) anyway.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .errors import InvalidSpotifyURLError, SpotifyLookupError
from .models import Credit, TrackCredits, TrackMetadata

# open.spotify.com/track/<id>[?...]  |  spotify:track:<id>  |  bare 22-char id
_TRACK_URL_RE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z]{2}/)?track/|spotify:track:)"
    r"(?P<id>[A-Za-z0-9]{15,25})"
)
_BARE_ID_RE = re.compile(r"^[A-Za-z0-9]{15,25}$")


def parse_track_id(url_or_uri: str) -> str:
    """Extract a bare Spotify track id from a URL, URI, or already-bare id.

    Raises:
        InvalidSpotifyURLError: if no track id could be found.
    """
    candidate = url_or_uri.strip()
    match = _TRACK_URL_RE.search(candidate)
    if match:
        return match.group("id")
    if _BARE_ID_RE.match(candidate):
        return candidate
    raise InvalidSpotifyURLError(
        f"Could not parse a Spotify track id out of {url_or_uri!r}. "
        "Expected an open.spotify.com/track/... URL, a spotify:track:... URI, "
        "or a bare 22-character track id."
    )


def _first(*values: Any) -> Optional[Any]:
    for v in values:
        if v not in (None, "", []):
            return v
    return None


def _extract_artists(track: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    for key in ("artists", "with", "firstArtist", "otherArtists"):
        items = track.get(key)
        if isinstance(items, dict):
            items = items.get("items", [])
        if isinstance(items, list):
            for item in items:
                node = item.get("artist", item) if isinstance(item, dict) else item
                name = None
                if isinstance(node, dict):
                    name = node.get("profile", {}).get("name") or node.get("name")
                if name and name not in names:
                    names.append(name)
    return names


def _extract_album(track: Dict[str, Any]) -> Dict[str, Any]:
    album = track.get("album") or track.get("albumOfTrack") or {}
    return album if isinstance(album, dict) else {}


def normalize_track_info(raw: Dict[str, Any], track_id: str) -> TrackMetadata:
    """Turn SpotAPI's raw ``get_track_info`` payload into a :class:`TrackMetadata`.

    SpotAPI passes through Spotify's internal GraphQL/partner-API shape more
    or less verbatim, which varies a bit release to release, so this is
    deliberately defensive - it tries a handful of known key spellings and
    quietly moves on when a field isn't present rather than raising.
    """
    # Some SpotAPI responses nest the actual track under "data" / "trackUnion".
    track = raw
    for key in ("data", "trackUnion", "track"):
        if isinstance(track.get(key), dict):
            track = track[key]

    album = _extract_album(track)
    name = _first(track.get("name"), raw.get("name"))
    artists = _extract_artists(track)
    album_name = _first(album.get("name"))
    album_artists = _extract_artists(album) if album else []
    duration_ms = _first(
        track.get("duration_ms"),
        (track.get("duration") or {}).get("totalMilliseconds") if isinstance(track.get("duration"), dict) else None,
    )
    release_date = _first(
        track.get("release_date"),
        ((album.get("date") or {}).get("isoString") if isinstance(album.get("date"), dict) else None),
    )
    external_ids: Dict[str, str] = {}
    isrc = None
    ext = track.get("external_ids") or track.get("externalIds")
    if isinstance(ext, dict):
        isrc = ext.get("isrc") or ext.get("ISRC")
        external_ids.update({k: v for k, v in ext.items() if isinstance(v, str)})
    elif isinstance(ext, list):
        for item in ext:
            if isinstance(item, dict) and item.get("type", "").lower() == "isrc":
                isrc = item.get("id")
                external_ids["isrc"] = isrc

    return TrackMetadata(
        name=name,
        artists=artists,
        album=album_name,
        album_artists=album_artists,
        release_date=release_date,
        duration_ms=duration_ms,
        isrc=isrc,
        label=_first(track.get("label")),
        explicit=_first(track.get("explicit"), track.get("isExplicit")),
        spotify_id=track_id,
        spotify_url=f"https://open.spotify.com/track/{track_id}",
        external_ids=external_ids,
        # The normalized fields and separate credits object are the only source
        # data needed by the result or AI prompt. SpotAPI's raw payload also
        # contains unrelated album/artist discographies and track listings.
        raw=None,
    )


def _try_fetch_spotify_credits(song: Any, track_id: str) -> TrackCredits:
    """Best-effort attempt at Spotify's undocumented track-credits endpoint.

    This piggybacks on the same authenticated/browser-like ``requests``-style
    session SpotAPI already established for us (``song.base.client``), so we
    don't do a second, separate login. If SpotAPI's internals don't expose
    what we expect (attribute names *do* change between SpotAPI releases),
    or Spotify rejects/reshapes the request, we degrade to "unavailable"
    instead of raising - credits are a bonus signal, the AI research step is
    the real source of truth for licensing.
    """
    note = (
        "SpotAPI fetched track metadata, but songwriter/producer credits were not available. "
        "SpotAPI documents get_track_info() for metadata; the request to Spotify's "
        "undocumented private credits endpoint failed or returned no usable roleCredits data."
    )
    client = getattr(getattr(song, "base", None), "client", None)
    if client is None:
        note = (
            "SpotAPI fetched track metadata, but this installed version did not expose the "
            "private Spotify session needed for credits. SpotAPI's documented get_track_info() "
            "method does not include songwriter/producer credits."
        )
        return TrackCredits(available=False, source_note=note)

    endpoint = f"https://spclient.wg.spotify.com/track-credits-view/v0/experimental/{track_id}/credits"
    try:
        resp = client.get(endpoint, params={"format": "json"})
        payload = getattr(resp, "response", None) or getattr(resp, "text", None)
        data = json.loads(payload) if isinstance(payload, str) else payload
        if not isinstance(data, dict):
            return TrackCredits(available=False, source_note=note)
    except Exception:
        return TrackCredits(available=False, source_note=note)

    roles = data.get("roleCredits") or data.get("roles") or []
    performers: List[Credit] = []
    songwriters: List[Credit] = []
    producers: List[Credit] = []
    for role_block in roles:
        if not isinstance(role_block, dict):
            continue
        role_title = (role_block.get("roleTitle") or "").strip().lower()
        for artist in role_block.get("artists", []):
            if not isinstance(artist, dict):
                continue
            credit = Credit(
                name=artist.get("name", "Unknown"),
                role=role_block.get("roleTitle", "Unknown"),
                spotify_uri=artist.get("uri"),
            )
            if "writer" in role_title or "composer" in role_title or "lyricist" in role_title:
                songwriters.append(credit)
            elif "producer" in role_title:
                producers.append(credit)
            else:
                performers.append(credit)

    if not (performers or songwriters or producers):
        return TrackCredits(available=False, source_note=note)

    return TrackCredits(
        available=True,
        source_note="fetched from Spotify's track-credits panel data",
        performers=performers,
        songwriters=songwriters,
        producers=producers,
        source_label=data.get("sourceNames") and ", ".join(data.get("sourceNames", [])) or None,
    )


class SpotifySource:
    """High-level entry point: Spotify URL/URI/id -> (:class:`TrackMetadata`, :class:`TrackCredits`)."""

    def __init__(self, *, language: str = "en") -> None:
        try:
            from spotapi import Song
        except ImportError as exc:  # pragma: no cover - environment issue, not logic
            raise SpotifyLookupError(
                "The 'spotapi' package is required (pip install spotapi)."
            ) from exc
        self._Song = Song
        self._language = language

    def fetch(self, url_or_uri: str) -> tuple[TrackMetadata, TrackCredits]:
        """Fetch and normalize everything we can get for a single track."""
        track_id = parse_track_id(url_or_uri)

        try:
            song = self._Song(language=self._language)
            raw = song.get_track_info(track_id)
        except Exception as exc:
            raise SpotifyLookupError(
                f"Failed to fetch Spotify track info for id {track_id!r}: {exc}"
            ) from exc

        if not isinstance(raw, dict):
            raise SpotifyLookupError(
                f"Unexpected response type from SpotAPI for track {track_id!r}: {type(raw)!r}"
            )

        metadata = normalize_track_info(raw, track_id)
        credits_ = _try_fetch_spotify_credits(song, track_id)

        # Credits panel is missing/blocked -> at minimum surface the artists
        # we already have as "performers" so the AI has something to anchor on.
        if not credits_.available and metadata.artists:
            credits_.performers = [Credit(name=a, role="Performer") for a in metadata.artists]

        return metadata, credits_
