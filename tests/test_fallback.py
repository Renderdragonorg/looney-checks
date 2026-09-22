"""Tests for the AI fallback chain (no network, no API key needed)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from music_copyright_checker.errors import (
    AIResearchError,
    AIResponseParseError,
    AllBackendsFailedError,
)
from music_copyright_checker.fallback_researcher import FallbackResearcher
from music_copyright_checker.models import LookupRequest, ResearchResult, TrackCredits, TrackMetadata
from music_copyright_checker.pipeline import Pipeline


def _request() -> LookupRequest:
    return LookupRequest(
        source="file",
        input_ref="song.mp3",
        track=TrackMetadata(name="Song", artists=["Artist"]),
        credits=TrackCredits(),
    )


class _StubResearcher:
    def __init__(self, label: str, *, result=None, error=None):
        self.provider_label = label
        self.mode = label.lower()
        self.calls = 0
        self._result = result
        self._error = error

    def research(self, request):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._result, {"mode": self.mode, "model": "stub-model", "session": "s"}


class _SequenceResearcher:
    """Raises the queued errors in order, then returns the result."""

    def __init__(self, label: str, *, errors=(), result=None):
        self.provider_label = label
        self.mode = label.lower()
        self.calls = 0
        self._errors = list(errors)
        self._result = result

    def research(self, request):
        self.calls += 1
        if self.calls <= len(self._errors):
            raise self._errors[self.calls - 1]
        return self._result, {"mode": self.mode, "model": "stub-model", "session": "s"}


class TestFallbackResearcher(unittest.TestCase):
    def test_primary_success_does_not_touch_secondary(self):
        primary = _StubResearcher("Primary", result=ResearchResult(status="complete", summary="ok"))
        secondary = _StubResearcher("Secondary", error=AIResearchError("nope"))
        chain = FallbackResearcher([primary, secondary])

        result, meta = chain.research(_request())

        self.assertEqual(result.status, "complete")
        self.assertEqual(primary.calls, 1)
        self.assertEqual(secondary.calls, 0)
        self.assertFalse(meta["fallback_used"])
        self.assertEqual(meta["provider"], "Primary")

    def test_primary_failure_falls_back(self):
        primary = _StubResearcher("Primary", error=AIResearchError("primary down"))
        secondary = _StubResearcher("Secondary", result=ResearchResult(status="partial", summary="ok"))
        chain = FallbackResearcher([primary, secondary])

        result, meta = chain.research(_request())

        self.assertEqual(result.status, "partial")
        self.assertEqual(primary.calls, 1)
        self.assertEqual(secondary.calls, 1)
        self.assertTrue(meta["fallback_used"])
        self.assertEqual(meta["provider"], "Secondary")
        self.assertEqual(meta["fallback_attempts"], 2)
        self.assertEqual(meta["fallback_failures"][0]["provider"], "Primary")

    def test_all_failures_raise(self):
        chain = FallbackResearcher(
            [
                _StubResearcher("First", error=AIResearchError("one")),
                _StubResearcher("Second", error=AIResearchError("two")),
            ]
        )
        with self.assertRaises(AllBackendsFailedError) as caught:
            chain.research(_request())
        self.assertIn("First", str(caught.exception))
        self.assertIn("Second", str(caught.exception))

    def test_requires_at_least_one(self):
        with self.assertRaises(ValueError):
            FallbackResearcher([])

    def test_empty_completion_retries_same_backend(self):
        primary = _SequenceResearcher(
            "Primary",
            errors=[AIResearchError("OpenRouter returned an empty completion.")],
            result=ResearchResult(status="complete", summary="ok"),
        )
        secondary = _StubResearcher("Secondary", result=ResearchResult(status="partial", summary="other"))
        chain = FallbackResearcher([primary, secondary], retries_per_backend=1, retry_delay_seconds=0)

        result, meta = chain.research(_request())

        self.assertEqual(result.status, "complete")
        self.assertEqual(primary.calls, 2)
        self.assertEqual(secondary.calls, 0)
        self.assertFalse(meta["fallback_used"])

    def test_parse_error_retries_same_backend(self):
        primary = _SequenceResearcher(
            "Primary",
            errors=[AIResponseParseError("Could not find a valid JSON object in the AI's response.")],
            result=ResearchResult(status="complete", summary="ok"),
        )
        secondary = _StubResearcher("Secondary", result=ResearchResult(status="partial", summary="other"))
        chain = FallbackResearcher([primary, secondary], retries_per_backend=1, retry_delay_seconds=0)

        result, meta = chain.research(_request())

        self.assertEqual(result.status, "complete")
        self.assertEqual(primary.calls, 2)
        self.assertEqual(secondary.calls, 0)
        self.assertFalse(meta["fallback_used"])

    def test_transient_exhausted_then_falls_back(self):
        primary = _StubResearcher("Primary", error=AIResearchError("OpenRouter returned an empty completion."))
        secondary = _StubResearcher("Secondary", result=ResearchResult(status="partial", summary="ok"))
        chain = FallbackResearcher([primary, secondary], retries_per_backend=1, retry_delay_seconds=0)

        result, meta = chain.research(_request())

        self.assertEqual(result.status, "partial")
        self.assertEqual(primary.calls, 2)
        self.assertEqual(secondary.calls, 1)
        self.assertTrue(meta["fallback_used"])

    def test_non_transient_is_not_retried(self):
        primary = _StubResearcher("Primary", error=AIResearchError("bad request: invalid track"))
        secondary = _StubResearcher("Secondary", result=ResearchResult(status="partial", summary="ok"))
        chain = FallbackResearcher([primary, secondary], retries_per_backend=3, retry_delay_seconds=0)

        chain.research(_request())

        self.assertEqual(primary.calls, 1)
        self.assertEqual(secondary.calls, 1)

    def test_retries_can_be_disabled(self):
        primary = _StubResearcher("Primary", error=AIResearchError("empty completion"))
        secondary = _StubResearcher("Secondary", result=ResearchResult(status="partial", summary="ok"))
        chain = FallbackResearcher([primary, secondary], retries_per_backend=0, retry_delay_seconds=0)

        chain.research(_request())

        self.assertEqual(primary.calls, 1)
        self.assertEqual(secondary.calls, 1)


class TestPipelineFallback(unittest.TestCase):
    def test_single_backend_is_not_wrapped(self):
        pipeline = Pipeline(cache_enabled=False)
        self.assertFalse(isinstance(pipeline._ai, FallbackResearcher))

    def test_fallback_backends_build_chain(self):
        pipeline = Pipeline(
            ai_backend="openrouter",
            ai_fallback_backends=["opencode-go", "openai-compatible"],
            openai_compatible_model="gpt-4o-mini",
            cache_enabled=False,
        )
        self.assertIsInstance(pipeline._ai, FallbackResearcher)
        self.assertEqual([r.mode for r in pipeline._ai.researchers], ["openrouter", "opencode-go", "openai-compatible"])
        # The cache identity covers the whole chain.
        self.assertIn("openai-compatible:gpt-4o-mini", pipeline._ai_cache_identity)

    def test_per_backend_model_overrides(self):
        pipeline = Pipeline(
            ai_backend="openrouter",
            ai_model="openrouter/free",
            ai_fallback_backends=["opencode-go"],
            ai_models={"opencode-go": "mimo-v2.5-pro"},
            cache_enabled=False,
        )
        self.assertEqual(pipeline._ai_model, "openrouter/free")
        self.assertIn("opencode-go:mimo-v2.5-pro", pipeline._ai_cache_identity)

    def test_invalid_fallback_backend_rejected(self):
        with self.assertRaises(ValueError):
            Pipeline(ai_backend="openrouter", ai_fallback_backends=["nope"], cache_enabled=False)


if __name__ == "__main__":
    unittest.main()
