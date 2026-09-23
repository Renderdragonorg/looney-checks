"""Builds the prompt sent to the AI (opencode) for the licensing research step."""

from __future__ import annotations

import json
from typing import Any, Dict

RESULT_SCHEMA_HINT = """{
  "status": "complete | partial | not_found",
  "summary": "max 600 characters; concise final finding",
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
    "video_verdict": "free_to_use | permitted_with_conditions | clearance_required | potentially_usable_with_platform_license | likely_not_permitted_without_permission | unknown",
    "social_media_verdict": "free_to_use | permitted_with_conditions | clearance_required | potentially_usable_with_platform_license | likely_not_permitted_without_permission | unknown",
    "reality_tv_verdict": "free_to_use | permitted_with_conditions | clearance_required | potentially_usable_with_platform_license | likely_not_permitted_without_permission | unknown",
    "sync_license_required": true,
    "master_license_required": true,
    "creator_declared_license": "the creator/rights-holder's own stated terms (e.g. 'royalty-free, credit appreciated'), or null",
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
RESEARCH_PROMPT_VERSION = "6"

MAX_DESCRIPTION_IN_PROMPT = 4000


def _source_license_block(request_payload: Dict[str, Any]) -> str:
    """Surface the source description and any detected licence terms for the AI.

    The full video description is included (not only regex-matched lines) so the
    model always reads it for permissions the creator stated in prose, while the
    detected statements call out the free-use terms explicitly.
    """
    track = request_payload.get("track")
    if not isinstance(track, dict):
        return ""
    description = track.get("description")
    description = description.strip() if isinstance(description, str) else ""
    statements = [s for s in (track.get("license_statements") or []) if isinstance(s, str) and s.strip()]
    if not description and not statements:
        return ""

    parts = [
        "\nSource text to check for usage terms. Read the video description and the",
        "creator/rights-holder comments for any licence or permission they state, and",
        "reflect it in the usage assessment. Do not ignore it:",
    ]
    if description:
        parts.append(f"\nDescription:\n{description[:MAX_DESCRIPTION_IN_PROMPT]}")
    if statements:
        lines = "\n".join(f"- {statement}" for statement in statements[:5])
        parts.append(f"\nDetected licence statements:\n{lines}")
    parts.append("")
    return "\n".join(parts)


def build_research_prompt(request_payload: Dict[str, Any]) -> str:
    """Compose the natural-language instructions + JSON payload for the AI agent."""
    payload_json = json.dumps(request_payload, indent=2, ensure_ascii=False)
    declared_block = _source_license_block(request_payload)

    return f"""You are a backend music-rights research worker. Search the web privately, then
return one compact machine-readable result. Do not narrate your process, quote pages, explain
your reasoning, or produce markdown.

Research task:
1. Identify the track from the supplied metadata.
   If the source is a YouTube video, the title and channel/uploader are noisy:
   strip marketing/format text such as "(Official Music Video)", "(Lyrics)",
   "[HD]", remaster/live/cover markers, and extract the underlying song title
   and artists. Corroborate them with the channel, description, tags, duration,
   or authoritative sources before identifying the track. Treat the channel as
   the uploader, which may be a label or a "- Topic" auto-generated channel
   rather than the performing artist.
   If the source is a local file and the title is derived from its filename,
   use that filename title as a search hint. It is not verified metadata: corroborate
   it with artist, album, ISRC, duration, or authoritative sources before identifying the track.
2. Visit up to 5 relevant authoritative sources, prioritizing official label/publisher/artist
   pages, PRO databases, and MusicBrainz. Use secondary sources only for corroboration.
3. Check for a creator/rights-holder usage licence before assuming clearance is required.
   Look at the supplied description, `license_statements`, and `top_comments` (the uploader's
   pinned/top comment often states terms such as "royalty free", "free to use in your videos,
   reactions, or streams", "free with credit", or a Creative Commons licence). A declaration
   by the uploader/rights holder is a licensing signal: record its terms in
   `creator_declared_license`, corroborate it against the artist's own pages when possible,
   and reflect it in the verdicts. If the declaration covers personal/creator video and
   streaming use but not broadcast or commercial TV, say so. Do NOT treat
   `licensed_content: true` (Content ID registration) as proof that reuse is restricted:
   rights holders can register a recording with Content ID and still grant free-use terms.
   If this video is a track from an album (the description usually names it), also check
   the album's own full-album video, Bandcamp, or artist page for a blanket creator
   licence covering the whole album; if a clear rights-holder declaration is found there,
   apply it to this track and cite that source.
4. After identification, assess practical use in an online video, on social-media platforms
   (YouTube, TikTok, Instagram, Facebook), and in a reality-TV episode.
   Separate composition/sync rights from the sound-recording/master rights. For a known
   copyrighted recording, assume both require clearance unless a reliable source proves a
   specific licence or exception (a creator declaration counts when corroborated).
   Verdicts: use `free_to_use` when the rights holder grants free use with no payment;
   `permitted_with_conditions` when reuse is allowed but conditioned (attribution,
   noncommercial, cover/remix only, no Content ID registration, platform-limited, etc.);
   `clearance_required` when permission is needed; and keep
   `likely_not_permitted_without_permission` for a known restricted recording.
5. Distinguish legal permission from platform behavior: Content ID claims, monetization sharing,
   a platform music-library license, short incidental use, or a video remaining online do not by
   themselves grant general permission. Platform-library permissions may be limited to one
   platform, account type, territory, duration, or noncommercial use. Reality TV normally needs broader negotiated rights for
   the episode, territory, term, edits, trailers/promos, broadcasters, and cue sheets.
6. Report only claims you actually verified. Never invent URLs, rights holders, contacts, or
   licensing terms. If uncertain, use null and a short warning. When a creator declaration is
   the only evidence, cite its source (the video page) and note that it is creator-stated.
7. Every source used must appear in "sources" with its exact URL and the claim it supports.

Output rules:
- Return ONLY one valid JSON object. No prose, markdown fences, or trailing text.
- Use the exact keys below; do not add keys.
- Keep summary under 600 characters, notes/supports under 240 characters, and warnings to 3.
- Keep usage caveats short and practical; do not provide legal advice or long explanations.
- Return at most 5 matches and 10 sources.
- Use [] for no results and null for unknown scalar values.

{RESULT_SCHEMA_HINT}

Track metadata to research:
{declared_block}
{payload_json}
"""
