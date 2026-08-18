"""Tests for persistent cache keys and pipeline-level AI deduplication."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from music_copyright_checker.cache import (
    CacheStore,
    hash_file,
    metadata_cache_key,
    research_cache_key,
)
from music_copyright_checker.models import (
    Credit,
    LookupRequest,
    ResearchResult,
    TrackCredits,
    TrackMetadata,
)
from music_copyright_checker.pipeline import Pipeline
from music_copyright_checker.prompts import RESEARCH_PROMPT_VERSION


class _FakeSpotify:
    def __init__(self):
        self.calls = 0

    def fetch(self, value):
        self.calls += 1
        return (
            TrackMetadata(
                name="Test Song",
                artists=["Artist"],
                isrc="US-ABC-12-34567",
                spotify_id="track-id",
            ),
            TrackCredits(performers=[Credit(name="Artist", role="Performer")]),
        )


class _FakeAI:
    def __init__(self):
        self.calls = 0

    def research(self, request):
        self.calls += 1
        time.sleep(0.03)
        return ResearchResult(status="complete", summary="Cached finding."), {
            "model": "test-model",
            "cost": 1.25,
            "tokens": {"input": 10, "output": 4},
        }


def _pipeline(cache: CacheStore, spotify: _FakeSpotify, ai: _FakeAI) -> Pipeline:
    pipeline = object.__new__(Pipeline)
    pipeline._spotify = spotify
    pipeline._file = None
    pipeline._run_ai_research = True
    pipeline._opencode_model = "test-model"
    pipeline._metadata_ttl_seconds = 3600
    pipeline._file_metadata_ttl_seconds = 3600
    pipeline._research_ttl_seconds = 3600
    pipeline._cache = cache
    from music_copyright_checker.cache import InFlight

    pipeline._in_flight = InFlight()
    pipeline._ai = ai
    return pipeline


class TestCacheStore(unittest.TestCase):
    def test_entries_persist_and_expire(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "cache.sqlite3")
            first = CacheStore(path)
            first.set("key", {"value": 1}, 60)
            second = CacheStore(path)
            self.assertEqual(second.get("key").value["value"], 1)
            second.set("expired", {"value": 2}, 0)
            self.assertIsNone(second.get("expired"))

    def test_file_hash_is_content_based(self):
        with tempfile.TemporaryDirectory() as directory:
            first_path = Path(directory) / "one.mp3"
            second_path = Path(directory) / "renamed.mp3"
            first_path.write_bytes(b"same audio bytes")
            second_path.write_bytes(b"same audio bytes")
            self.assertEqual(hash_file(str(first_path)), hash_file(str(second_path)))

    def test_research_key_excludes_input_reference(self):
        first = TrackMetadata(name="Song", artists=["Artist"], isrc="US123")
        request_a = LookupRequest(
            source="spotify",
            input_ref="https://open.spotify.com/track/id",
            track=first,
            credits=TrackCredits(),
        )
        request_b = LookupRequest(
            source="spotify",
            input_ref="spotify:track:id",
            track=first,
            credits=TrackCredits(),
        )
        self.assertEqual(
            research_cache_key(
                request_a, model="model", prompt_version=RESEARCH_PROMPT_VERSION
            ),
            research_cache_key(
                request_b, model="model", prompt_version=RESEARCH_PROMPT_VERSION
            ),
        )
        self.assertEqual(metadata_cache_key("spotify", "id"), "metadata:v1:spotify:id")


class TestPipelineCaching(unittest.TestCase):
    TRACK_ID = "6rqhFgbbKwnb9MLmUQDhG6"

    def test_metadata_and_ai_results_are_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheStore(str(Path(directory) / "cache.sqlite3"))
            spotify = _FakeSpotify()
            ai = _FakeAI()
            pipeline = _pipeline(cache, spotify, ai)

            first = pipeline.check_spotify_url(f"https://open.spotify.com/track/{self.TRACK_ID}")
            second = pipeline.check_spotify_url(f"spotify:track:{self.TRACK_ID}")

            self.assertEqual(spotify.calls, 1)
            self.assertEqual(ai.calls, 1)
            self.assertEqual(first.research.summary, second.research.summary)
            self.assertFalse(first.ai_meta["cache_hit"])
            self.assertTrue(second.ai_meta["cache_hit"])
            self.assertEqual(second.ai_meta["cost"], 0.0)
            self.assertTrue(second.ai_meta["metadata_cache_hit"])

    def test_concurrent_requests_only_run_ai_once(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheStore(str(Path(directory) / "cache.sqlite3"))
            spotify = _FakeSpotify()
            ai = _FakeAI()
            pipeline = _pipeline(cache, spotify, ai)
            results = []

            def run():
                results.append(pipeline.check_spotify_url(f"spotify:track:{self.TRACK_ID}"))

            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(len(results), 2)
            self.assertEqual(ai.calls, 1)


if __name__ == "__main__":
    unittest.main()
