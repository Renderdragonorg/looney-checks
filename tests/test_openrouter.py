"""Tests for the direct OpenRouter REST backend (no network, no API key needed)."""

from __future__ import annotations

import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from music_copyright_checker.ai_researcher import AIResearcher
from music_copyright_checker.errors import OpenRouterError, AIResearchError
from music_copyright_checker.models import LookupRequest, TrackCredits, TrackMetadata
from music_copyright_checker.openrouter_client import (
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_OPENROUTER_TIMEOUT,
    OpenRouterClient,
)
from music_copyright_checker.openrouter_researcher import OpenRouterResearcher
from music_copyright_checker.pipeline import Pipeline

RESEARCH_JSON = {
    "status": "complete",
    "summary": "Found the rights holder.",
    "matches": [
        {
            "source_name": "Label",
            "source_url": "https://label.example/track",
            "confidence": "high",
            "rights_holder": "Label Inc",
        }
    ],
    "sources": [
        {
            "name": "Label",
            "url": "https://label.example/track",
            "source_type": "label",
            "supports": "Rights holder",
        }
    ],
    "usage_assessment": {
        "video_verdict": "clearance_required",
        "social_media_verdict": "clearance_required",
        "reality_tv_verdict": "clearance_required",
        "sync_license_required": True,
        "master_license_required": True,
        "caveats": ["Clear both sides."],
    },
    "official_licensing_contacts": ["https://label.example/licensing"],
    "warnings": [],
}


def _completion(text: str, *, model: str = "openrouter/free", cost: float = 0.0) -> dict:
    return {
        "id": "gen-abc123",
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 40,
            "total_tokens": 160,
            "cost": cost,
            "completion_tokens_details": {"reasoning_tokens": 5},
        },
    }


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _request() -> LookupRequest:
    return LookupRequest(
        source="youtube",
        input_ref="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        track=TrackMetadata(name="Never Gonna Give You Up", artists=["Rick Astley"], youtube_id="dQw4w9WgXcQ"),
        credits=TrackCredits(),
    )


class TestOpenRouterClient(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(DEFAULT_OPENROUTER_MODEL, "openrouter/free")
        self.assertEqual(DEFAULT_OPENROUTER_TIMEOUT, 300.0)

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_key_raises(self):
        client = OpenRouterClient()
        with self.assertRaises(OpenRouterError) as caught:
            client.call("hi")
        self.assertIn("OPENROUTER_API_KEY", str(caught.exception))

    @patch("music_copyright_checker.openrouter_client.urlopen")
    def test_request_body_uses_free_router_and_web_search(self, urlopen):
        urlopen.return_value = _FakeResponse(_completion("hello"))
        client = OpenRouterClient(api_key="test-key")
        result = client.call("research this", model="openrouter/free", timeout=30.0)

        self.assertEqual(result.text, "hello")
        self.assertEqual(result.model, "openrouter/free")
        self.assertEqual(result.session, "gen-abc123")
        self.assertEqual(result.tokens["total"], 160)

        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "openrouter/free")
        self.assertEqual(body["messages"][-1]["content"], "research this")
        self.assertEqual(body["tools"][0]["type"], "openrouter:web_search")
        self.assertEqual(request.headers["Authorization"], "Bearer test-key")

    def test_parse_normalizes_cost_and_content_parts(self):
        payload = _completion("", cost=0.0021)
        payload["choices"][0]["message"]["content"] = [{"type": "text", "text": "part-a"}, {"type": "text", "text": "part-b"}]
        result = OpenRouterClient._parse(payload, "openrouter/free")
        self.assertEqual(result.text, "part-apart-b")
        self.assertEqual(result.cost, 0.0021)
        self.assertEqual(result.tokens["input"], 120)
        self.assertEqual(result.tokens["reasoning"], 5)

    @patch("music_copyright_checker.openrouter_client.urlopen")
    def test_auth_error_mentions_key(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",
            401,
            "Unauthorized",
            {},
            BytesIO(json.dumps({"error": {"message": "No auth credentials"}}).encode()),
        )
        with self.assertRaises(OpenRouterError) as caught:
            OpenRouterClient(api_key="bad").call("hi")
        self.assertIn("API key", str(caught.exception))

    @patch("music_copyright_checker.openrouter_client.urlopen")
    def test_rate_limit_is_retried(self, urlopen):
        error = HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",
            429,
            "Too Many Requests",
            {},
            BytesIO(b'{"error": {"message": "rate limited"}}'),
        )
        urlopen.side_effect = [error, _FakeResponse(_completion("recovered"))]
        with patch("music_copyright_checker.openrouter_client.time.sleep"):
            result = OpenRouterClient(api_key="k").call("hi")
        self.assertEqual(result.text, "recovered")
        self.assertEqual(urlopen.call_count, 2)

    @patch("music_copyright_checker.openrouter_client.urlopen")
    def test_empty_choices_raise(self, urlopen):
        urlopen.return_value = _FakeResponse({"choices": []})
        with self.assertRaises(OpenRouterError):
            OpenRouterClient(api_key="k").call("hi")


class TestOpenRouterResearcher(unittest.TestCase):
    @patch("music_copyright_checker.openrouter_client.urlopen")
    def test_research_parses_and_reports_meta(self, urlopen):
        urlopen.return_value = _FakeResponse(_completion(json.dumps(RESEARCH_JSON), model="openrouter/free"))
        researcher = OpenRouterResearcher(api_key="k")
        result, meta = researcher.research(_request())

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.matches[0].rights_holder, "Label Inc")
        self.assertEqual(meta["mode"], "openrouter")
        self.assertEqual(meta["model"], "openrouter/free")
        self.assertEqual(meta["session"], "gen-abc123")

        body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        user_text = body["messages"][-1]["content"]
        self.assertIn("Never Gonna Give You Up", user_text)

    @patch("music_copyright_checker.openrouter_client.urlopen")
    def test_track_raw_payload_is_not_sent(self, urlopen):
        urlopen.return_value = _FakeResponse(_completion(json.dumps(RESEARCH_JSON)))
        request = _request()
        request.track.raw = {"huge": "source payload"}
        OpenRouterResearcher(api_key="k").research(request)
        body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertNotIn("source payload", body["messages"][-1]["content"])

    @patch("music_copyright_checker.openrouter_client.urlopen")
    def test_empty_completion_raises_ai_research_error(self, urlopen):
        urlopen.return_value = _FakeResponse({"choices": [{"message": {"content": ""}}]})
        with self.assertRaises(AIResearchError):
            OpenRouterResearcher(api_key="k").research(_request())


class TestPipelineBackendSelection(unittest.TestCase):
    def test_openrouter_is_default_backend(self):
        pipeline = Pipeline(run_ai_research=False, cache_enabled=False)
        try:
            self.assertEqual(pipeline._ai_backend, "openrouter")
            self.assertEqual(pipeline._ai_model, "openrouter/free")
        finally:
            if pipeline._cache is not None:
                pipeline._cache.clear()

    def test_openrouter_backend_builds_openrouter_researcher(self):
        pipeline = Pipeline(cache_enabled=False)
        self.assertIsInstance(pipeline._ai, OpenRouterResearcher)

    def test_opencode_backend_still_available(self):
        pipeline = Pipeline(ai_backend="opencode", run_ai_research=False, cache_enabled=False)
        self.assertEqual(pipeline._ai_backend, "opencode")

    def test_unknown_backend_rejected(self):
        with self.assertRaises(ValueError):
            Pipeline(ai_backend="nope", run_ai_research=False, cache_enabled=False)


if __name__ == "__main__":
    unittest.main()
