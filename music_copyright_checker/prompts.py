"""Builds the prompt sent to the AI (opencode) for the licensing research step."""

from __future__ import annotations

import json
from typing import Any, Dict

RESULT_SCHEMA_HINT = """{
  "status": "complete | partial | not_found",
  "summary": "max 300 characters; concise final finding",
  "matches": [
    {
      "source_name": "source name",
      "source_url": "exact URL supporting this match",
      "confidence": "high | medium | low",
      "rights_holder": "string or null",
      "publisher": "string or null",
      "label": "string or null",
      "license_type": "string or null",
      "territory": "string or null",
      "notes": "max 240 characters or null"
    }
  ],
  "sources": [
    {
      "name": "source name",
      "url": "exact URL visited",
      "source_type": "official | PRO | database | label | publisher | other",
      "supports": "short claim supported, max 240 characters"
    }
  ],
  "usage_assessment": {
    "video_verdict": "clearance_required | potentially_usable_with_platform_license | likely_not_permitted_without_permission | unknown",
    "social_media_verdict": "clearance_required | potentially_usable_with_platform_license | likely_not_permitted_without_permission | unknown",
    "reality_tv_verdict": "clearance_required | potentially_usable_with_platform_license | likely_not_permitted_without_permission | unknown",
    "sync_license_required": true,
    "master_license_required": true,
    "platform_exception": "short explanation of platform/library/claim distinctions or null",
    "caveats": ["short practical caveats, max 5"]
  },
  "official_licensing_contacts": [
    "license-request URLs only"
  ],
  "warnings": [
    "short warnings, max 3"
  ]
}"""

# Bump this whenever the research instructions or output policy changes.
RESEARCH_PROMPT_VERSION = "1"


def build_research_prompt(request_payload: Dict[str, Any]) -> str:
    """Compose the natural-language instructions + JSON payload for the AI agent."""
    payload_json = json.dumps(request_payload, indent=2, ensure_ascii=False)

    return f"""You are a backend music-rights research worker. Search the web privately, then
return one compact machine-readable result. Do not narrate your process, quote pages, explain
your reasoning, or produce markdown.

Research task:
1. Identify the track from the supplied metadata.
   If the source is a local file and the title is derived from its filename,
   use that filename title as a search hint. It is not verified metadata: corroborate
   it with artist, album, ISRC, duration, or authoritative sources before identifying the track.
2. Visit up to 5 relevant authoritative sources, prioritizing official label/publisher/artist
   pages, PRO databases, and MusicBrainz. Use secondary sources only for corroboration.
3. After identification, assess practical use in an online video, on social-media platforms
   (YouTube, TikTok, Instagram, Facebook), and in a reality-TV episode.
   Separate composition/sync rights from the sound-recording/master rights. For a known
   copyrighted recording, assume both require clearance unless a reliable source proves a
   specific license or exception.
4. Distinguish legal permission from platform behavior: Content ID claims, monetization sharing,
   a platform music-library license, short incidental use, or a video remaining online do not by
   themselves grant general permission. Platform-library permissions may be limited to one
   platform, account type, territory, duration, or noncommercial use. Reality TV normally needs broader negotiated rights for
   the episode, territory, term, edits, trailers/promos, broadcasters, and cue sheets.
5. Report only claims you actually verified. Never invent URLs, rights holders, contacts, or
   licensing terms. If uncertain, use null and a short warning.
6. Every source used must appear in "sources" with its exact URL and the claim it supports.

Output rules:
- Return ONLY one valid JSON object. No prose, markdown fences, or trailing text.
- Use the exact keys below; do not add keys.
- Keep summary under 300 characters, notes/supports under 240 characters, and warnings to 3.
- Keep usage caveats short and practical; do not provide legal advice or long explanations.
- Return at most 5 matches and 10 sources.
- Use [] for no results and null for unknown scalar values.

{RESULT_SCHEMA_HINT}

Track metadata to research:

{payload_json}
"""
