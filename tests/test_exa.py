"""Tests for Exa-backed web search (no network, no API key needed)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from music_copyright_checker.errors import ExaSearchError, OpenAICompatibleError
from music_copyright_checker.exa_search import (
    DEFAULT_EXA_BASE_URL,
    ExaSearchClient,
    format_search_results,
)
from music_copyright_checker.openai_compatible_client import OpenAICompatibleClient
from music_copyright_checker.openai_compatible_researcher import OpenAICompatibleResearcher
from music_copyright_checker.pipeline import Pipeline

EXA_RESULTS = {
    "results": [
        {
            "title": "Label licensing",
            "url": "https://label.example/licensing",
            "publishedDate": "2024-01-02",
            "author": "Label",
            "text": "  Official licensing information.  ",
        },
        {"title": "No URL"},
    ]
}

FINAL_JSON = {
    "status": "complete",
    "summary": "Found the rights holder.",
    "matches": [],
    "sources": [],
    "usage_assessment": {},
    "official_licensing_contacts": [],
    "warnings": [],
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


def _completion(message: dict, *, model: str = "gpt-4o-mini", tokens=(10, 5)) -> dict:
    return {
        "id": "gen-1",
        "model": model,
        "choices": [{"message": message}],
        "usage": {
            "prompt_tokens": tokens[0],
            "completion_tokens": tokens[1],
            "total_tokens": tokens[0] + tokens[1],
        },
    }


def _headers(request) -> dict:
    return {key.lower(): value for key, value in request.headers.items()}


class TestExaSearchClient(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(DEFAULT_EXA_BASE_URL, "https://api.exa.ai")

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_key_raises(self):
        with self.assertRaises(ExaSearchError) as caught:
            ExaSearchClient().search("anything")
        self.assertIn("EXA_API_KEY", str(caught.exception))

    @patch("music_copyright_checker.exa_search.urlopen")
    def test_search_parses_results_and_sends_key(self, urlopen):
        urlopen.return_value = _FakeResponse(EXA_RESULTS)
        client = ExaSearchClient(api_key="exa-key")
        results = client.search("label licensing", num_results=3)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://label.example/licensing")
        self.assertEqual(results[0]["title"], "Label licensing")

        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["query"], "label licensing")
        self.assertEqual(body["numResults"], 3)
        self.assertEqual(_headers(request)["x-api-key"], "exa-key")
        self.assertTrue(request.full_url.endswith("/search"))

    def test_format_search_results_renders_urls(self):
        rendered = format_search_results(
            [{"title": "T", "url": "https://example.com", "text": "hello   world"}]
        )
        self.assertIn("https://example.com", rendered)
        self.assertIn("hello world", rendered)


class TestOpenAICompatibleClient(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    def test_model_required(self):
        with self.assertRaises(OpenAICompatibleError) as caught:
            OpenAICompatibleClient(api_key="k")
        self.assertIn("model", str(caught.exception).lower())

    @patch.dict("os.environ", {}, clear=True)
    @patch("music_copyright_checker.openrouter_client.urlopen")
    def test_exa_is_required_for_client_side_search(self, urlopen):
        urlopen.return_value = _FakeResponse(_completion({"role": "assistant", "content": "hi"}))
        client = OpenAICompatibleClient(api_key="k", model="gpt-4o-mini")
        with self.assertRaises(ExaSearchError):
            client.call("research this")
        urlopen.assert_not_called()

    @patch.dict("os.environ", {}, clear=True)
    @patch("music_copyright_checker.openrouter_client.urlopen")
    def test_server_search_rejected_without_server_support(self, urlopen):
        client = OpenAICompatibleClient(api_key="k", model="gpt-4o-mini", search_backend="server")
        with self.assertRaises(OpenAICompatibleError):
            client.call("research this")
        urlopen.assert_not_called()

    @patch.dict("os.environ", {"EXA_API_KEY": "exa-key"}, clear=True)
    @patch("music_copyright_checker.exa_search.urlopen")
    @patch("music_copyright_checker.openrouter_client.urlopen")
    def test_exa_tool_loop_feeds_results_back(self, client_urlopen, exa_urlopen):
        tool_call = _completion(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": json.dumps({"query": "rights"})},
                    }
                ],
            },
            tokens=(10, 5),
        )
        final = _completion({"role": "assistant", "content": json.dumps(FINAL_JSON)}, tokens=(20, 7))
        client_urlopen.side_effect = [_FakeResponse(tool_call), _FakeResponse(final)]
        exa_urlopen.return_value = _FakeResponse(EXA_RESULTS)

        client = OpenAICompatibleClient(api_key="k", model="gpt-4o-mini")
        result = client.call("research this")

        self.assertEqual(result.model, "gpt-4o-mini")
        self.assertEqual(result.tokens["total"], 42)
        self.assertIn("complete", result.text)

        first_body = json.loads(client_urlopen.call_args_list[0].args[0].data.decode("utf-8"))
        self.assertEqual(first_body["tools"][0]["type"], "function")
        self.assertEqual(first_body["tools"][0]["function"]["name"], "web_search")

        second_body = json.loads(client_urlopen.call_args_list[1].args[0].data.decode("utf-8"))
        self.assertEqual(second_body["messages"][-1]["role"], "tool")
        self.assertIn("https://label.example/licensing", second_body["messages"][-1]["content"])

        exa_body = json.loads(exa_urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(exa_body["query"], "rights")

    @patch.dict("os.environ", {"EXA_API_KEY": "exa-key"}, clear=True)
    @patch("music_copyright_checker.exa_search.urlopen")
    @patch("music_copyright_checker.openrouter_client.urlopen")
    def test_researcher_reports_meta(self, client_urlopen, exa_urlopen):
        client_urlopen.return_value = _FakeResponse(
            _completion({"role": "assistant", "content": json.dumps(FINAL_JSON)})
        )
        exa_urlopen.return_value = _FakeResponse(EXA_RESULTS)
        researcher = OpenAICompatibleResearcher(api_key="k", model="gpt-4o-mini")
        from music_copyright_checker.models import LookupRequest, TrackCredits, TrackMetadata

        request = LookupRequest(
            source="file",
            input_ref="x",
            track=TrackMetadata(name="Song", artists=["A"]),
            credits=TrackCredits(),
        )
        result, meta = researcher.research(request)
        self.assertEqual(result.status, "complete")
        self.assertEqual(meta["mode"], "openai-compatible")
        self.assertEqual(meta["model"], "gpt-4o-mini")
        self.assertEqual(researcher.mode, "openai-compatible")


class TestPipelineOpenAICompatible(unittest.TestCase):
    def test_backend_builds_researcher(self):
        pipeline = Pipeline(
            ai_backend="openai-compatible",
            openai_compatible_model="gpt-4o-mini",
            cache_enabled=False,
        )
        self.assertEqual(pipeline._ai_backend, "openai-compatible")
        self.assertEqual(pipeline._ai_model, "gpt-4o-mini")
        self.assertIsInstance(pipeline._ai, OpenAICompatibleResearcher)


if __name__ == "__main__":
    unittest.main()
