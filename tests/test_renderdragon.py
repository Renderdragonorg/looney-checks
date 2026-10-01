"""Tests for the RenderDragon music-link source, pipeline wiring, and caching.

No network is required: the HTTP layer is mocked. Run with:
    python -m pytest tests/test_renderdragon.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from music_copyright_checker.cache import CacheStore
from music_copyright_checker.errors import (
    InvalidRenderDragonLinkError,
    RenderDragonAPIError,
    RenderDragonLookupError,
)
from music_copyright_checker.models import track_metadata_from_dict
from music_copyright_checker.pipeline import Pipeline
from music_copyright_checker.renderdragon_source import (
    RenderDragonSource,
    build_music_link,
    is_allowlisted_source,
    normalize_music_link,
    parse_music_link,
    renderdragon_identity,
    to_raw_github_url,
)

RAW_URL = (
    "https://raw.githubusercontent.com/Yxmura/resources_renderdragon/"
    "refs/heads/main/music/11_AM.mp3"
)
LINK = (
    "https://renderdragon.org/api/music-link"
    "?name=11+AM&url=https%3A%2F%2Fraw.githubusercontent.com%2FYxmura%2F"
    "resources_renderdragon%2Frefs%2Fheads%2Fmain%2Fmusic%2F11_AM.mp3"
    "&credits=Nintendo&category=music&ext=mp3&file=11_AM.mp3&id=123"
)
PAYLOAD = {
    "service": "RenderDragon music link",
    "name": "11 AM - Animal Crossing City Folk OST",
    "credits": "Nintendo",
    "category": "music",
    "extension": "mp3",
    "filename": "11_AM_-_Animal_Crossing_City_Folk_OST.mp3",
    "direct_url": RAW_URL,
    "raw_reference": RAW_URL,
    "website_url": "https://renderdragon.org/resources?track=123",
    "source": "github",
    "available": True,
    "size": 3305600,
    "content_type": "audio/mpeg",
}


class TestParseMusicLink(unittest.TestCase):
    def test_parses_query_parameters(self):
        params = parse_music_link(LINK)
        self.assertEqual(params["name"], "11 AM")
        self.assertEqual(params["url"], RAW_URL)
        self.assertEqual(params["credits"], "Nintendo")
        self.assertEqual(params["id"], "123")

    def test_wrong_host_raises(self):
        with self.assertRaises(InvalidRenderDragonLinkError):
            parse_music_link("https://example.com/api/music-link?url=x")

    def test_wrong_path_raises(self):
        with self.assertRaises(InvalidRenderDragonLinkError):
            parse_music_link("https://renderdragon.org/resources?url=x")


class TestBuildMusicLink(unittest.TestCase):
    def test_maps_resource_fields(self):
        link = build_music_link(
            {
                "title": "11 AM - Animal Crossing City Folk OST",
                "download_url": "https://github.com/Yxmura/resources_renderdragon/blob/main/music/11_AM.mp3",
                "credit": "Nintendo",
                "category": "music",
                "filetype": "mp3",
                "filename": "11_AM.mp3",
                "id": 123,
            }
        )
        params = parse_music_link(link)
        self.assertEqual(params["name"], "11 AM - Animal Crossing City Folk OST")
        self.assertEqual(
            params["url"],
            "https://raw.githubusercontent.com/Yxmura/resources_renderdragon/main/music/11_AM.mp3",
        )
        self.assertEqual(params["credits"], "Nintendo")
        self.assertEqual(params["ext"], "mp3")
        self.assertEqual(params["id"], "123")

    def test_rejects_non_allowlisted_url(self):
        with self.assertRaises(InvalidRenderDragonLinkError):
            build_music_link({"name": "Song", "url": "https://example.com/song.mp3"})

    def test_validation_can_be_disabled(self):
        link = build_music_link(
            {"name": "Song", "url": "https://example.com/song.mp3"}, validate=False
        )
        self.assertIn("example.com", link)


class TestUrlHelpers(unittest.TestCase):
    def test_blob_to_raw(self):
        self.assertEqual(
            to_raw_github_url("https://github.com/Owner/Repo/blob/main/a/b.mp3"),
            "https://raw.githubusercontent.com/Owner/Repo/main/a/b.mp3",
        )

    def test_non_blob_unchanged(self):
        self.assertEqual(to_raw_github_url(RAW_URL), RAW_URL)

    def test_allowlist(self):
        self.assertTrue(is_allowlisted_source(RAW_URL))
        self.assertTrue(
            is_allowlisted_source(
                "https://raw.githubusercontent.com/Coder-soft/Minecraft-Creator-Safe-Playlist/main/x.mp3"
            )
        )
        self.assertFalse(is_allowlisted_source("https://raw.githubusercontent.com/other/repo/main/x.mp3"))
        self.assertFalse(is_allowlisted_source(""))


class TestNormalizeMusicLink(unittest.TestCase):
    def test_basic_shape(self):
        metadata, credits = normalize_music_link(PAYLOAD, track_id="123")
        self.assertEqual(metadata.name, "11 AM - Animal Crossing City Folk OST")
        self.assertEqual(metadata.artists, ["Nintendo"])
        self.assertEqual(metadata.category, "music")
        self.assertEqual(metadata.renderdragon_id, "123")
        self.assertEqual(metadata.audio_url, RAW_URL)
        self.assertEqual(metadata.website_url, "https://renderdragon.org/resources?track=123")
        self.assertEqual(metadata.filename, "11_AM_-_Animal_Crossing_City_Folk_OST.mp3")
        self.assertTrue(metadata.available)
        self.assertEqual(metadata.size_bytes, 3305600)
        self.assertEqual(metadata.content_type, "audio/mpeg")
        self.assertTrue(credits.available)
        self.assertEqual(credits.performers[0].name, "Nintendo")

    def test_null_size_and_content_type_tolerated(self):
        metadata, _credits = normalize_music_link(
            {**PAYLOAD, "size": None, "content_type": None, "direct_url": None}, track_id="123"
        )
        self.assertIsNone(metadata.size_bytes)
        self.assertIsNone(metadata.content_type)
        self.assertIsNone(metadata.audio_url)

    def test_round_trips_through_cache_dict(self):
        metadata, _credits = normalize_music_link(PAYLOAD, track_id="123")
        restored = track_metadata_from_dict(metadata.to_dict())
        self.assertEqual(restored.renderdragon_id, "123")
        self.assertEqual(restored.audio_url, RAW_URL)
        self.assertEqual(restored.size_bytes, 3305600)


class TestRenderDragonSource(unittest.TestCase):
    def setUp(self):
        self.source = RenderDragonSource()

    @patch("music_copyright_checker.renderdragon_source._request_json")
    def test_fetch_requests_json(self, request_json):
        request_json.return_value = PAYLOAD
        metadata, _credits = self.source.fetch(LINK)
        self.assertEqual(metadata.renderdragon_id, "123")
        requested_url = request_json.call_args.args[0]
        self.assertTrue(requested_url.startswith("https://renderdragon.org/api/music-link?"))
        self.assertIn("url=", requested_url)

    def test_fetch_params_requires_url(self):
        with self.assertRaises(InvalidRenderDragonLinkError):
            self.source.fetch_params({"name": "Song"})

    @patch("music_copyright_checker.renderdragon_source._request_json")
    def test_unavailable_track_raises(self, request_json):
        request_json.return_value = {**PAYLOAD, "available": False}
        with self.assertRaises(RenderDragonLookupError):
            self.source.fetch(LINK)


class TestRequestJsonErrors(unittest.TestCase):
    @patch("music_copyright_checker.renderdragon_source.urlopen")
    def test_allowlist_error_becomes_api_error(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://renderdragon.org/api/music-link",
            400,
            "Bad Request",
            {},
            BytesIO(b"{}"),
        )
        from music_copyright_checker.renderdragon_source import _request_json

        with self.assertRaises(RenderDragonAPIError) as caught:
            _request_json("https://renderdragon.org/api/music-link?url=x", 5.0)
        self.assertIn("allowlist", str(caught.exception))

    @patch("music_copyright_checker.renderdragon_source.urlopen")
    def test_missing_url_error(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://renderdragon.org/api/music-link",
            422,
            "Unprocessable Entity",
            {},
            BytesIO(b"{}"),
        )
        from music_copyright_checker.renderdragon_source import _request_json

        with self.assertRaises(RenderDragonAPIError) as caught:
            _request_json("https://renderdragon.org/api/music-link", 5.0)
        self.assertIn("missing", str(caught.exception))


class TestIdentity(unittest.TestCase):
    def test_id_preferred(self):
        self.assertEqual(renderdragon_identity({"id": "123", "url": "x"}), "id:123")

    def test_url_fallback(self):
        self.assertEqual(renderdragon_identity({"url": RAW_URL}), f"url:{RAW_URL}")

    def test_params_digest(self):
        self.assertTrue(renderdragon_identity({"name": "Song"}).startswith("params:"))


class TestPipelineRenderDragon(unittest.TestCase):
    def test_check_uses_normalized_metadata_without_ai(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(RenderDragonSource, "fetch_params", return_value=normalize_music_link(PAYLOAD, track_id="123")):
                pipeline = Pipeline(
                    run_ai_research=False,
                    cache_enabled=True,
                    cache_path=str(Path(directory) / "cache.sqlite3"),
                )
                first = pipeline.check_renderdragon_url(LINK)
                second = pipeline.check_renderdragon_url(LINK)

            self.assertEqual(first.request.source, "renderdragon")
            self.assertEqual(first.request.track.renderdragon_id, "123")
            self.assertEqual(first.request.track.audio_url, RAW_URL)
            self.assertFalse(first.ai_meta["metadata_cache_hit"])
            self.assertTrue(second.ai_meta["metadata_cache_hit"])


class TestPipelineCacheKey(unittest.TestCase):
    def test_metadata_cache_reused_across_links(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheStore(str(Path(directory) / "cache.sqlite3"))
            with patch.object(RenderDragonSource, "fetch_params", return_value=normalize_music_link(PAYLOAD, track_id="123")) as fetch:
                pipeline = Pipeline(run_ai_research=False, cache_store=cache)
                pipeline.check_renderdragon_url(LINK)
                pipeline.check_renderdragon_url(LINK)
            self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
