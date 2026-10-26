"""Tests for the subscription-library free-use correction guard.

Epidemic Sound and similar catalogs are "royalty-free" only for creators with
an active paid subscription, so their tracks must never be reported
`free_to_use` - even when a cached AI result says so.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from music_copyright_checker.models import (
    CopyrightCheckResult,
    LicenseMatch,
    LookupRequest,
    ResearchResult,
    TrackCredits,
    TrackMetadata,
    UsageAssessment,
)
from music_copyright_checker.pipeline import (
    RESEARCH_PROMPT_VERSION,
    _guard_subscription_library_result,
)


def _result(
    video_verdict: str,
    label: str | None = None,
    matches: list[LicenseMatch] | None = None,
) -> CopyrightCheckResult:
    return CopyrightCheckResult(
        request=LookupRequest(
            source="spotify",
            input_ref="https://open.spotify.com/track/6rqhFgbbKwnb9MLmUQDhG6",
            track=TrackMetadata(name="Test Track", label=label),
            credits=TrackCredits(),
        ),
        research=ResearchResult(
            status="complete",
            usage_assessment=UsageAssessment(
                video_verdict=video_verdict,
                social_media_verdict=video_verdict,
                reality_tv_verdict=video_verdict,
            ),
            matches=matches or [],
        ),
    )


class TestSubscriptionLibraryGuard(unittest.TestCase):
    def test_epidemic_sound_label_downgrades_free_to_use(self):
        result = _result("free_to_use", label="Epidemic Sound")
        _guard_subscription_library_result(result)
        self.assertEqual(result.research.usage_assessment.video_verdict, "clearance_required")
        self.assertEqual(result.research.usage_assessment.social_media_verdict, "clearance_required")
        self.assertEqual(result.research.usage_assessment.reality_tv_verdict, "clearance_required")
        self.assertTrue(
            any("Epidemic Sound" in c for c in result.research.usage_assessment.caveats)
        )

    def test_match_label_downgrades_even_when_track_label_unknown(self):
        result = _result(
            "free_to_use",
            matches=[
                LicenseMatch(
                    source_name="Epidemic Sound catalog",
                    publisher="Epidemic Sound",
                    confidence="high",
                )
            ],
        )
        _guard_subscription_library_result(result)
        self.assertEqual(result.research.usage_assessment.video_verdict, "clearance_required")

    def test_other_subscription_libraries_caught(self):
        for label in ("Artlist", "MUSICBED", "Soundstripe", "Uppbeat", "PremiumBeat"):
            with self.subTest(label=label):
                result = _result("free_to_use", label=label)
                _guard_subscription_library_result(result)
                self.assertEqual(
                    result.research.usage_assessment.video_verdict, "clearance_required"
                )

    def test_non_free_verdicts_untouched(self):
        result = _result("clearance_required", label="Epidemic Sound")
        _guard_subscription_library_result(result)
        self.assertEqual(result.research.usage_assessment.video_verdict, "clearance_required")
        self.assertEqual(result.research.usage_assessment.caveats, [])

    def test_free_track_without_library_label_untouched(self):
        result = _result("free_to_use", label="Chill🍓 Records")
        _guard_subscription_library_result(result)
        self.assertEqual(result.research.usage_assessment.video_verdict, "free_to_use")
        self.assertEqual(result.research.usage_assessment.caveats, [])

    def test_caveat_added_only_once(self):
        result = _result("free_to_use", label="Epidemic Sound")
        _guard_subscription_library_result(result)
        _guard_subscription_library_result(result)
        warnings = [
            c for c in result.research.usage_assessment.caveats if "Epidemic Sound" in c
        ]
        self.assertEqual(len(warnings), 1)

    def test_prompt_version_bumped_for_subscription_rule(self):
        self.assertGreaterEqual(int(RESEARCH_PROMPT_VERSION), 7)


if __name__ == "__main__":
    unittest.main()
