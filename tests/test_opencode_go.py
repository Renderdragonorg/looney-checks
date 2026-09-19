"""Tests for the direct OpenCode Go REST backend (no network, no API key needed)."""

from __future__ import annotations

import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from music_copyright_checker.errors import AIResearchError, OpenCodeGoError
from music_copyright_checker.models import LookupRequest, TrackCredits, TrackMetadata
from music_copyright_checker.opencode_go_client import (
    DEFAULT_OPENCODE_GO_BASE_URL,
    DEFAULT_OPENCODE_GO_MAX_TOKENS,
    DEFAULT_OPENCODE_GO_MODEL,
    DEFAULT_OPENCODE_GO_TIMEOUT,
    OpenCodeGoClient,
)
from music_copyright_checker.opencode_go_researcher import OpenCodeGoResearcher
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


def _completion(text: str, *, model: str = "mimo-v2.5", cost: float = 0.0) -> dict:
    return {
        "id": "gen-abc123",
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 40,
            "total_tokens": 160,
            "cost": cost,
            "completion_tokens_details": {"reasoning_tokens": 0},
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


def _headers(request) -> dict:
    return {key.lower(): value for key, value in request.headers.items()}


def _request() -> LookupRequest:
    return LookupRequest(
        source="youtube",
        input_ref="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        track=TrackMetadata(name="Never Gonna Give You Up", artists=["Rick Astley"], youtube_id="dQw4w9WgXcQ"),
        credits=TrackCredits(),
    )


class TestOpenCodeGoClient(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(DEFAULT_OPENCODE_GO_MODEL, "mimo-v2.5")
        self.assertEqual(DEFAULT_OPENCODE_GO_BASE_URL, "https://opencode.ai/zen/go/v1")
        self.assertEqual(DEFAULT_OPENCODE_GO_TIMEOUT, 300.0)
        self.assertEqual(DEFAULT_OPENCODE_GO_MAX_TOKENS, 8000)

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_key_raises(self):
        with self.assertRaises(OpenCodeGoError) as caught:
            OpenCodeGoClient().call("hi")
        self.assertIn("OPENCODE_GO_API_KEY", str(caught.exception))

    @patch("music_copyright_checker.openrouter_client.urlopen")
    def test_request_uses_headers_model_and_disabled_reasoning(self, urlopen):
        urlopen.return_value = _FakeResponse(_completion("hello"))
        client = OpenCodeGoClient(api_key="test-key", session="sess-123", user_agent="ua-test/1.0")
        result = client.call("research this", timeout=30.0)

        self.assertEqual(result.text, "hello")
        self.assertEqual(client.mode, "opencode-go")

        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "mimo-v2.5")
        self.assertEqual(body["reasoning"], {"enabled": False})
        self.assertEqual(body["max_tokens"], 8000)
        self.assertEqual(body["tools"][0]["type"], "openrouter:web_search")

        headers = _headers(request)
        self.assertEqual(headers["authorization"], "Bearer test-key")
        self.assertEqual(headers["user-agent"], "ua-test/1.0")
        self.assertEqual(headers["x-opencode-session"], "sess-123")
        self.assertTrue(request.full_url.endswith("/zen/go/v1/chat/completions"))

    @patch("music_copyright_checker.openrouter_client.urlopen")
    def test_auth_error_mentions_key(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://opencode.ai/zen/go/v1/chat/completions",
            401,
            "Unauthorized",
            {},
            BytesIO(json.dumps({"error": {"message": "No auth credentials"}}).encode()),
        )
        with self.assertRaises(OpenCodeGoError) as caught:
            OpenCodeGoClient(api_key="bad").call("hi")
        self.assertIn("OpenCode Go", str(caught.exception))
        self.assertIn("OPENCODE_GO_API_KEY", str(caught.exception))

    @patch("music_copyright_checker.openrouter_client.urlopen")
    def test_empty_choices_raise(self, urlopen):
        urlopen.return_value = _FakeResponse({"choices": []})
        with self.assertRaises(OpenCodeGoError):
            OpenCodeGoClient(api_key="k").call("hi")


class TestOpenCodeGoResearcher(unittest.TestCase):
    @patch("music_copyright_checker.openrouter_client.urlopen")
    def test_research_parses_and_reports_meta(self, urlopen):
        urlopen.return_value = _FakeResponse(_completion(json.dumps(RESEARCH_JSON)))
        researcher = OpenCodeGoResearcher(api_key="k")
        result, meta = researcher.research(_request())

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.matches[0].rights_holder, "Label Inc")
        self.assertEqual(meta["mode"], "opencode-go")
        self.assertEqual(meta["model"], "mimo-v2.5")

        body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertIn("Never Gonna Give You Up", body["messages"][-1]["content"])

    @patch("music_copyright_checker.openrouter_client.urlopen")
    def test_empty_completion_raises_ai_research_error(self, urlopen):
        urlopen.return_value = _FakeResponse({"choices": [{"message": {"content": ""}}]})
        with self.assertRaises(AIResearchError):
            OpenCodeGoResearcher(api_key="k").research(_request())


class TestPipelineOpenCodeGoBackend(unittest.TestCase):
    def test_opencode_go_backend_selects_model_and_researcher(self):
        pipeline = Pipeline(ai_backend="opencode-go", cache_enabled=False)
        try:
            self.assertEqual(pipeline._ai_backend, "opencode-go")
            self.assertEqual(pipeline._ai_model, "mimo-v2.5")
            self.assertIsInstance(pipeline._ai, OpenCodeGoResearcher)
        finally:
            pass

    def test_opencode_go_model_override(self):
        pipeline = Pipeline(ai_backend="opencode-go", ai_model="mimo-v2.5-pro", run_ai_research=False, cache_enabled=False)
        self.assertEqual(pipeline._ai_model, "mimo-v2.5-pro")


if __name__ == "__main__":
    unittest.main()
