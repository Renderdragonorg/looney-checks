"""Lightweight tests that don't require spotapi / opencode_harness / mutagen
to be installed - they only exercise pure-Python parsing/normalization logic.
Run with: python -m pytest tests/  (or just `python tests/test_basic.py`)
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from music_copyright_checker.ai_researcher import (
    DEFAULT_OPENCODE_MODEL,
    DEFAULT_OPENCODE_TIMEOUT,
    parse_research_response,
)
from music_copyright_checker.spotify_source import normalize_track_info, parse_track_id
from music_copyright_checker.file_source import _json_safe_tag
from music_copyright_checker.errors import InvalidSpotifyURLError, AIResponseParseError


class TestOpenCodeDefaults(unittest.TestCase):
    def test_big_pickle_is_default_model(self):
        self.assertEqual(DEFAULT_OPENCODE_MODEL, "opencode/big-pickle")

    def test_research_timeout_allows_long_file_research(self):
        self.assertEqual(DEFAULT_OPENCODE_TIMEOUT, 900.0)


class TestParseTrackId(unittest.TestCase):
    def test_open_spotify_url(self):
        self.assertEqual(
            parse_track_id("https://open.spotify.com/track/6rqhFgbbKwnb9MLmUQDhG6"),
            "6rqhFgbbKwnb9MLmUQDhG6",
        )

    def test_url_with_query_string(self):
        self.assertEqual(
            parse_track_id("https://open.spotify.com/track/6rqhFgbbKwnb9MLmUQDhG6?si=abc123"),
            "6rqhFgbbKwnb9MLmUQDhG6",
        )

    def test_intl_locale_url(self):
        self.assertEqual(
            parse_track_id("https://open.spotify.com/intl-de/track/6rqhFgbbKwnb9MLmUQDhG6"),
            "6rqhFgbbKwnb9MLmUQDhG6",
        )

    def test_uri(self):
        self.assertEqual(
            parse_track_id("spotify:track:6rqhFgbbKwnb9MLmUQDhG6"),
            "6rqhFgbbKwnb9MLmUQDhG6",
        )

    def test_bare_id(self):
        self.assertEqual(parse_track_id("6rqhFgbbKwnb9MLmUQDhG6"), "6rqhFgbbKwnb9MLmUQDhG6")

    def test_invalid(self):
        with self.assertRaises(InvalidSpotifyURLError):
            parse_track_id("https://open.spotify.com/playlist/notatrack")


class TestFileTagSerialization(unittest.TestCase):
    def test_unusual_mutagen_values_become_json_safe(self):
        value = _json_safe_tag({"frame": [b"Artist", object()]})
        self.assertEqual(value["frame"][0], "Artist")
        self.assertIsInstance(value["frame"][1], str)


class TestNormalizeTrackInfo(unittest.TestCase):
    def test_basic_shape(self):
        raw = {
            "name": "Test Song",
            "artists": {"items": [{"profile": {"name": "Artist One"}}, {"profile": {"name": "Artist Two"}}]},
            "album": {"name": "Test Album", "date": {"isoString": "2020-01-01"}},
            "duration_ms": 210000,
            "external_ids": {"isrc": "USABC1234567"},
        }
        meta = normalize_track_info(raw, "abc123")
        self.assertEqual(meta.name, "Test Song")
        self.assertEqual(meta.artists, ["Artist One", "Artist Two"])
        self.assertEqual(meta.album, "Test Album")
        self.assertEqual(meta.release_date, "2020-01-01")
        self.assertEqual(meta.duration_ms, 210000)
        self.assertEqual(meta.isrc, "USABC1234567")
        self.assertEqual(meta.spotify_id, "abc123")
        self.assertEqual(meta.spotify_url, "https://open.spotify.com/track/abc123")

    def test_nested_under_data(self):
        raw = {"data": {"trackUnion": {"name": "Nested Song", "artists": {"items": []}}}}
        meta = normalize_track_info(raw, "xyz")
        self.assertEqual(meta.name, "Nested Song")

    def test_current_spotify_first_artist_shape(self):
        raw = {
            "data": {
                "trackUnion": {
                    "name": "Nested Song",
                    "firstArtist": {"items": [{"profile": {"name": "Pink Floyd"}}]},
                    "otherArtists": {"items": [{"profile": {"name": "Guest Artist"}}]},
                }
            }
        }
        meta = normalize_track_info(raw, "xyz")
        self.assertEqual(meta.artists, ["Pink Floyd", "Guest Artist"])

    def test_missing_fields_dont_raise(self):
        meta = normalize_track_info({}, "id1")
        self.assertIsNone(meta.name)
        self.assertEqual(meta.artists, [])

    def test_normalized_track_does_not_retain_raw_spotify_payload(self):
        raw = {
            "data": {
                "trackUnion": {
                    "name": "Single Track",
                    "albumOfTrack": {
                        "name": "Album",
                        "tracks": {"items": [{"track": {"name": "Other Track"}}]},
                    },
                }
            }
        }

        meta = normalize_track_info(raw, "track-id")

        self.assertIsNone(meta.raw)


class TestParseResearchResponse(unittest.TestCase):
    def test_clean_json(self):
        payload = {
            "status": "complete",
            "summary": "Found it.",
            "matches": [
                {
                    "source_name": "MusicBrainz",
                    "source_url": "https://musicbrainz.org/x",
                    "confidence": "high",
                    "rights_holder": "Some Label",
                }
            ],
            "sources": [
                {
                    "name": "MusicBrainz",
                    "url": "https://musicbrainz.org/x",
                    "source_type": "database",
                    "supports": "Recording identity",
                }
            ],
            "usage_assessment": {
                "video_verdict": "clearance_required",
                "social_media_verdict": "potentially_usable_with_platform_license",
                "reality_tv_verdict": "likely_not_permitted_without_permission",
                "sync_license_required": True,
                "master_license_required": True,
                "platform_exception": "A platform claim is not a license.",
                "caveats": ["Clear both composition and master rights."],
            },
            "official_licensing_contacts": ["https://label.example/licensing"],
            "warnings": [],
        }
        result = parse_research_response(json.dumps(payload))
        self.assertEqual(result.summary, "Found it.")
        self.assertEqual(result.status, "complete")
        self.assertEqual(len(result.matches), 1)
        self.assertEqual(result.matches[0].rights_holder, "Some Label")
        self.assertEqual(result.sources[0].url, "https://musicbrainz.org/x")
        self.assertEqual(result.usage_assessment.video_verdict, "clearance_required")
        self.assertEqual(result.usage_assessment.social_media_verdict, "potentially_usable_with_platform_license")
        self.assertTrue(result.usage_assessment.master_license_required)
        self.assertEqual(result.official_licensing_contacts, ["https://label.example/licensing"])
        self.assertNotIn("raw_ai_text", result.to_dict())

    def test_parser_compacts_and_derives_sources(self):
        result = parse_research_response(
            json.dumps(
                {
                    "status": "unexpected",
                    "summary": "  A   concise finding. ",
                    "matches": [
                        {
                            "source_name": "Source",
                            "source_url": "https://source.example/track",
                            "notes": "  note with   extra whitespace  ",
                        }
                    ],
                    "official_licensing_contacts": "not-a-list",
                    "warnings": [" first warning "],
                    "usage_assessment": {"sync_license_required": "yes"},
                }
            )
        )
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.summary, "A concise finding.")
        self.assertEqual(result.sources[0].url, "https://source.example/track")
        self.assertEqual(result.warnings, ["first warning"])
        self.assertIsNone(result.usage_assessment.sync_license_required)

    def test_json_in_code_fence(self):
        payload = {"status": "not_found", "summary": "ok", "matches": [], "sources": [], "official_licensing_contacts": [], "warnings": []}
        text = f"Here you go:\n```json\n{json.dumps(payload)}\n```\nHope that helps!"
        result = parse_research_response(text)
        self.assertEqual(result.summary, "ok")

    def test_json_with_surrounding_prose(self):
        payload = {"status": "not_found", "summary": "ok", "matches": [], "sources": [], "official_licensing_contacts": [], "warnings": []}
        text = f"Sure! {json.dumps(payload)} Let me know if you need more."
        result = parse_research_response(text)
        self.assertEqual(result.summary, "ok")

    def test_json_encoded_as_a_string(self):
        payload = {"status": "complete", "summary": "wrapped", "matches": [], "sources": [], "official_licensing_contacts": [], "warnings": []}
        result = parse_research_response(json.dumps(json.dumps(payload)))
        self.assertEqual(result.summary, "wrapped")

    def test_garbage_raises(self):
        with self.assertRaises(AIResponseParseError):
            parse_research_response("not json at all, sorry")


if __name__ == "__main__":
    unittest.main()
