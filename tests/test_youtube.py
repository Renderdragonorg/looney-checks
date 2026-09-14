"""Tests for the YouTube Data API v3 source, pipeline wiring, and caching.

No network or API key is required: the API layer is mocked. Run with:
    python -m pytest tests/test_youtube.py
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

from music_copyright_checker.cache import CacheStore, metadata_cache_key
from music_copyright_checker.errors import (
    InvalidYouTubeURLError,
    YouTubeAPIError,
    YouTubeLookupError,
)
from music_copyright_checker.models import (
    Credit,
    LookupRequest,
    ResearchResult,
    TrackCredits,
    TrackMetadata,
)
from music_copyright_checker.pipeline import Pipeline
from music_copyright_checker.youtube_source import (
    YouTubeSource,
    build_youtube_credits,
    iso8601_duration_to_ms,
    label_from_description,
    normalize_video_info,
    parse_video_id,
    resolve_api_key,
)


class TestParseVideoId(unittest.TestCase):
    def test_watch_url(self):
        self.assertEqual(
            parse_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_watch_url_with_extra_params(self):
        self.assertEqual(
            parse_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s&list=abc"),
            "dQw4w9WgXcQ",
        )

    def test_short_url(self):
        self.assertEqual(parse_video_id("https://youtu.be/dQw4w9WgXcQ?si=abc"), "dQw4w9WgXcQ")

    def test_shorts_url(self):
        self.assertEqual(parse_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_embed_url(self):
        self.assertEqual(parse_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_live_url(self):
        self.assertEqual(parse_video_id("https://www.youtube.com/live/dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_music_youtube_url(self):
        self.assertEqual(
            parse_video_id("https://music.youtube.com/watch?v=dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_bare_id(self):
        self.assertEqual(parse_video_id("dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_invalid_raises(self):
        with self.assertRaises(InvalidYouTubeURLError):
            parse_video_id("https://www.youtube.com/playlist?list=PL123")


class TestDurationParsing(unittest.TestCase):
    def test_minutes_and_seconds(self):
        self.assertEqual(iso8601_duration_to_ms("PT3M21S"), 201000)

    def test_hours(self):
        self.assertEqual(iso8601_duration_to_ms("PT1H2M3S"), 3723000)

    def test_days(self):
        self.assertEqual(iso8601_duration_to_ms("P1DT1S"), 86401000)

    def test_zero_live_placeholder(self):
        self.assertEqual(iso8601_duration_to_ms("P0D"), 0)

    def test_invalid(self):
        self.assertIsNone(iso8601_duration_to_ms("not-a-duration"))
        self.assertIsNone(iso8601_duration_to_ms(None))


class TestNormalizeVideoInfo(unittest.TestCase):
    RAW = {
        "id": "dQw4w9WgXcQ",
        "snippet": {
            "title": "Rick Astley - Never Gonna Give You Up (Official Video)",
            "channelTitle": "Rick Astley",
            "channelId": "UCuAXFkgsw1L7xaCfnd5JJOw",
            "description": "The official video.\nProvided to YouTube by Sony Music",
            "tags": ["rick astley", "never gonna give you up"],
            "categoryId": "10",
            "publishedAt": "2009-10-25T06:57:33Z",
            "thumbnails": {
                "default": {"url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/default.jpg"},
                "high": {"url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"},
            },
        },
        "contentDetails": {"duration": "PT3M33S", "licensedContent": True},
        "statistics": {"viewCount": "1500000000"},
        "topicDetails": {"topicCategories": ["https://en.wikipedia.org/wiki/Music"]},
    }

    def test_basic_shape(self):
        meta = normalize_video_info(self.RAW, "dQw4w9WgXcQ")
        self.assertEqual(meta.name, "Rick Astley - Never Gonna Give You Up (Official Video)")
        self.assertEqual(meta.artists, ["Rick Astley"])
        self.assertEqual(meta.channel, "Rick Astley")
        self.assertEqual(meta.channel_id, "UCuAXFkgsw1L7xaCfnd5JJOw")
        self.assertEqual(meta.channel_url, "https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw")
        self.assertEqual(meta.youtube_id, "dQw4w9WgXcQ")
        self.assertEqual(meta.youtube_url, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(meta.duration_ms, 213000)
        self.assertEqual(meta.release_date, "2009-10-25T06:57:33Z")
        self.assertEqual(meta.category, "Music")
        self.assertTrue(meta.licensed_content)
        self.assertEqual(meta.view_count, 1500000000)
        self.assertEqual(meta.tags, ["rick astley", "never gonna give you up"])
        self.assertEqual(meta.thumbnail_url, "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg")

    def test_missing_fields_dont_raise(self):
        meta = normalize_video_info({}, "abc12345678")
        self.assertIsNone(meta.name)
        self.assertEqual(meta.artists, [])
        self.assertIsNone(meta.duration_ms)
        self.assertIsNone(meta.view_count)

    def test_category_id_fallback(self):
        raw = {"snippet": {"categoryId": "24"}}
        self.assertEqual(normalize_video_info(raw, "abc12345678").category, "Entertainment")

    def test_description_is_truncated(self):
        raw = {"snippet": {"description": "x" * 5000}}
        self.assertEqual(len(normalize_video_info(raw, "abc12345678").description), 2000)


class TestCredits(unittest.TestCase):
    def test_label_extraction(self):
        self.assertEqual(
            label_from_description("Some text\nProvided to YouTube by Sony Music\nMore"),
            "Sony Music",
        )
        self.assertIsNone(label_from_description("no label line here"))

    def test_build_credits(self):
        meta = TrackMetadata(channel="Rick Astley", description="Provided to YouTube by Sony Music")
        credits = build_youtube_credits(meta)
        self.assertTrue(credits.available)
        self.assertEqual(credits.performers, [Credit(name="Rick Astley", role="Uploader")])
        self.assertEqual(credits.source_label, "Sony Music")


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestYouTubeSource(unittest.TestCase):
    def setUp(self):
        self.source = YouTubeSource(api_key="test-key")

    @patch("music_copyright_checker.youtube_source._request_json")
    def test_fetch_video_requests_videos_endpoint(self, request_json):
        request_json.return_value = {"items": [TestNormalizeVideoInfo.RAW]}
        meta, credits = self.source.fetch_video("dQw4w9WgXcQ")
        self.assertEqual(meta.youtube_id, "dQw4w9WgXcQ")
        self.assertTrue(credits.available)
        path, params, _timeout = request_json.call_args.args
        self.assertEqual(path, "videos")
        self.assertEqual(params["id"], "dQw4w9WgXcQ")
        self.assertEqual(params["key"], "test-key")
        self.assertIn("snippet", params["part"])

    @patch("music_copyright_checker.youtube_source._request_json")
    def test_fetch_video_missing_raises(self, request_json):
        request_json.return_value = {"items": []}
        with self.assertRaises(YouTubeLookupError):
            self.source.fetch_video("dQw4w9WgXcQ")

    @patch("music_copyright_checker.youtube_source._request_json")
    def test_search_video_uses_music_category(self, request_json):
        request_json.return_value = {"items": [{"id": {"videoId": "dQw4w9WgXcQ"}}]}
        self.assertEqual(self.source.search_video("Rick Astley"), "dQw4w9WgXcQ")
        _path, params, _timeout = request_json.call_args.args
        self.assertEqual(params["videoCategoryId"], "10")
        self.assertEqual(params["q"], "Rick Astley")

    @patch("music_copyright_checker.youtube_source._request_json")
    def test_search_video_falls_back_without_category(self, request_json):
        request_json.side_effect = [
            {"items": []},
            {"items": [{"id": {"videoId": "dQw4w9WgXcQ"}}]},
        ]
        self.assertEqual(self.source.search_video("Obscure Song"), "dQw4w9WgXcQ")
        self.assertEqual(request_json.call_count, 2)
        self.assertNotIn("videoCategoryId", request_json.call_args.args[1])

    @patch("music_copyright_checker.youtube_source._request_json")
    def test_search_video_no_results_raises(self, request_json):
        request_json.return_value = {"items": []}
        with self.assertRaises(YouTubeLookupError):
            self.source.search_video("nothing at all")

    @patch("music_copyright_checker.youtube_source._request_json")
    def test_resolve_video_id_searches_for_free_text(self, request_json):
        request_json.return_value = {"items": [{"id": {"videoId": "dQw4w9WgXcQ"}}]}
        self.assertEqual(self.source.resolve_video_id("Rick Astley - Never Gonna Give You Up"), "dQw4w9WgXcQ")


class TestRequestJsonErrors(unittest.TestCase):
    @patch("music_copyright_checker.youtube_source.urlopen")
    def test_http_error_becomes_youtube_api_error(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://www.googleapis.com/youtube/v3/videos",
            403,
            "Forbidden",
            {},
            BytesIO(json.dumps({"error": {"message": "API key invalid"}}).encode()),
        )
        from music_copyright_checker.youtube_source import _request_json

        with self.assertRaises(YouTubeAPIError) as caught:
            _request_json("videos", {"id": "x", "key": "bad"}, 5.0)
        self.assertIn("API key invalid", str(caught.exception))

    @patch("music_copyright_checker.youtube_source.urlopen")
    def test_error_payload_becomes_youtube_api_error(self, urlopen):
        urlopen.return_value = _FakeResponse({"error": {"message": "quota exceeded"}})
        from music_copyright_checker.youtube_source import _request_json

        with self.assertRaises(YouTubeAPIError):
            _request_json("videos", {"id": "x", "key": "bad"}, 5.0)


class TestApiKeyResolution(unittest.TestCase):
    def test_explicit_key_wins(self):
        self.assertEqual(resolve_api_key("explicit"), "explicit")

    @patch.dict("os.environ", {"YOUTUBE_API_KEY": "from-env"})
    def test_env_override(self):
        self.assertEqual(resolve_api_key(None), "from-env")

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_key_raises(self):
        with self.assertRaises(YouTubeLookupError) as caught:
            resolve_api_key(None)
        self.assertIn("YOUTUBE_API_KEY", str(caught.exception))


class _FakeYouTube:
    def __init__(self):
        self.searches = 0
        self.fetches = 0

    def resolve_video_id(self, value):
        try:
            return parse_video_id(value)
        except InvalidYouTubeURLError:
            return self.search_video(value)

    def search_video(self, query):
        self.searches += 1
        return "dQw4w9WgXcQ"

    def fetch_video(self, video_id):
        self.fetches += 1
        return (
            TrackMetadata(
                name="Never Gonna Give You Up",
                artists=["Rick Astley"],
                youtube_id=video_id,
                youtube_url=f"https://www.youtube.com/watch?v={video_id}",
                channel="Rick Astley",
            ),
            TrackCredits(performers=[Credit(name="Rick Astley", role="Uploader")]),
        )


class _FakeAI:
    def __init__(self):
        self.calls = 0

    def research(self, request):
        self.calls += 1
        return ResearchResult(status="complete", summary="Cached YouTube finding."), {
            "model": "test-model",
            "cost": 0.02,
            "tokens": {"input": 12, "output": 6},
        }


def _pipeline(cache, youtube, ai) -> Pipeline:
    pipeline = object.__new__(Pipeline)
    pipeline._spotify = None
    pipeline._youtube = youtube
    pipeline._file = None
    pipeline._run_ai_research = True
    pipeline._ai_model = "test-model"
    pipeline._metadata_ttl_seconds = 3600
    pipeline._file_metadata_ttl_seconds = 3600
    pipeline._research_ttl_seconds = 3600
    pipeline._cache = cache
    from music_copyright_checker.cache import InFlight

    pipeline._in_flight = InFlight()
    pipeline._ai = ai
    return pipeline


class TestPipelineYouTube(unittest.TestCase):
    URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_metadata_and_ai_results_are_reused_across_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheStore(str(Path(directory) / "cache.sqlite3"))
            youtube = _FakeYouTube()
            ai = _FakeAI()
            pipeline = _pipeline(cache, youtube, ai)

            first = pipeline.check_youtube_url(self.URL)
            second = pipeline.check_youtube_url("dQw4w9WgXcQ")

            self.assertEqual(youtube.fetches, 1)
            self.assertEqual(ai.calls, 1)
            self.assertEqual(first.request.source, "youtube")
            self.assertEqual(first.research.summary, second.research.summary)
            self.assertTrue(second.ai_meta["cache_hit"])
            self.assertTrue(second.ai_meta["metadata_cache_hit"])

    def test_free_text_query_is_resolved_and_query_mapping_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheStore(str(Path(directory) / "cache.sqlite3"))
            youtube = _FakeYouTube()
            ai = _FakeAI()
            pipeline = _pipeline(cache, youtube, ai)

            first = pipeline.check_youtube_url("Rick Astley - Never Gonna Give You Up")
            second = pipeline.check_youtube_url("Rick Astley - Never Gonna Give You Up")

            self.assertEqual(youtube.searches, 1)
            self.assertEqual(youtube.fetches, 1)
            self.assertEqual(ai.calls, 1)
            self.assertEqual(first.request.track.youtube_id, "dQw4w9WgXcQ")
            self.assertIsNotNone(cache.get(metadata_cache_key("youtube-query", "rick astley - never gonna give you up")))
            self.assertTrue(second.ai_meta["cache_hit"])

    def test_json_response_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheStore(str(Path(directory) / "cache.sqlite3"))
            pipeline = _pipeline(cache, _FakeYouTube(), _FakeAI())
            payload = pipeline.check_youtube_url(self.URL).to_dict()

            self.assertEqual(payload["request"]["source"], "youtube")
            self.assertEqual(payload["request"]["track"]["youtube_id"], "dQw4w9WgXcQ")
            self.assertEqual(payload["research"]["status"], "complete")
            self.assertIn("ai_meta", payload)


if __name__ == "__main__":
    unittest.main()
