"""Runs the licensing research step through an `opencode` agent via opencode-harness.

opencode-harness (see the vendored docs this package was built against, under
``docs/opencode-harness/``) is a thin stdlib-only wrapper around the
`opencode` CLI / `opencode serve` REST API. We use it to hand the agent a
track's metadata and let it use its own web-browsing/search tools to go
research licensing information across multiple sources, then hand back
strict JSON that we parse into a :class:`~music_copyright_checker.models.ResearchResult`.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from .errors import AIResearchError, AIResponseParseError
from .models import LicenseMatch, LookupRequest, ResearchResult, ResearchSource, UsageAssessment
from .prompts import build_research_prompt

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
DEFAULT_OPENCODE_MODEL = "opencode/big-pickle"
DEFAULT_OPENCODE_TIMEOUT = 900.0


def _text(value: Any, *, default: Optional[str] = None, limit: Optional[int] = None) -> Optional[str]:
    if not isinstance(value, str):
        return default
    value = " ".join(value.split())
    return value[:limit] if limit else value


def _text_list(value: Any, *, limit: int = 3, item_limit: int = 240) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_text(v, limit=item_limit) for v in value) if item][:limit]


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _optional_bool(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _extract_json_object(text: str) -> Dict[str, Any]:
    """Pull a JSON object out of the AI's reply, tolerating stray prose/fences.

    Agents are instructed to reply with *only* JSON, but we don't want the
    whole pipeline to blow up if a model wraps it in a code fence or adds a
    stray sentence, so we try, in order: the raw text as-is, the contents of
    a ```json fence, and finally the first {...} balanced-brace span.
    """
    candidates = [text.strip()]

    fence_match = _JSON_FENCE_RE.search(text)
    if fence_match:
        candidates.insert(0, fence_match.group(1).strip())

    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidates.append(text[first_brace : last_brace + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, str):
            try:
                unwrapped = json.loads(parsed)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(unwrapped, dict):
                return unwrapped

    raise AIResponseParseError(
        "Could not find a valid JSON object in the AI's response. "
        f"Raw response started with: {text[:200]!r}"
    )


def parse_research_response(text: str) -> ResearchResult:
    data = _extract_json_object(text)

    matches = []
    for item in _items(data.get("matches"))[:5]:
        if not isinstance(item, dict):
            continue
        matches.append(
            LicenseMatch(
                source_name=_text(item.get("source_name"), default="Unknown source") or "Unknown source",
                source_url=_text(item.get("source_url")),
                confidence=_text(item.get("confidence"), default="low") or "low",
                rights_holder=_text(item.get("rights_holder")),
                publisher=_text(item.get("publisher")),
                label=_text(item.get("label")),
                license_type=_text(item.get("license_type")),
                territory=_text(item.get("territory")),
                notes=_text(item.get("notes"), limit=240),
            )
        )

    sources = []
    for item in _items(data.get("sources"))[:10]:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"))
        url = _text(item.get("url"))
        if name and url:
            sources.append(
                ResearchSource(
                    name=name,
                    url=url,
                    source_type=_text(item.get("source_type"), default="other") or "other",
                    supports=_text(item.get("supports"), limit=240),
                )
            )

    # Keep source citations available even if the model only used the match shape.
    known_urls = {source.url for source in sources}
    for match in matches:
        if match.source_url and match.source_url not in known_urls:
            sources.append(ResearchSource(name=match.source_name, url=match.source_url))
            known_urls.add(match.source_url)

    usage = data.get("usage_assessment")
    if not isinstance(usage, dict):
        usage = {}

    status = _text(data.get("status"), default="not_found") or "not_found"
    if status not in {"complete", "partial", "not_found"}:
        status = "partial"

    return ResearchResult(
        status=status,
        summary=_text(data.get("summary"), default="", limit=300) or "",
        matches=matches,
        sources=sources[:10],
        usage_assessment=UsageAssessment(
            video_verdict=_text(usage.get("video_verdict"), default="unknown") or "unknown",
            social_media_verdict=_text(usage.get("social_media_verdict"), default="unknown") or "unknown",
            reality_tv_verdict=_text(usage.get("reality_tv_verdict"), default="unknown") or "unknown",
            sync_license_required=_optional_bool(usage.get("sync_license_required")),
            master_license_required=_optional_bool(usage.get("master_license_required")),
            platform_exception=_text(usage.get("platform_exception"), limit=300),
            caveats=_text_list(usage.get("caveats"), limit=5),
        ),
        official_licensing_contacts=_text_list(data.get("official_licensing_contacts"), limit=5),
        warnings=_text_list(data.get("warnings")),
    )


class AIResearcher:
    """Wraps ``opencode_harness.OpenCode`` to run the licensing-research prompt."""

    def __init__(
        self,
        *,
        server: Optional[str] = None,
        binary: str = "opencode",
        model: Optional[str] = DEFAULT_OPENCODE_MODEL,
        auto_approve: bool = True,
        timeout: float = DEFAULT_OPENCODE_TIMEOUT,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        try:
            from opencode_harness import OpenCode
        except ImportError as exc:
            raise AIResearchError(
                "The 'opencode_harness' package is required. It is vendored in "
                "this distribution (reinstall with `pip install -e .`), and the "
                "`opencode` CLI (or a running `opencode serve`) must be available."
            ) from exc

        self._client = OpenCode(
            server=server,
            binary=binary,
            auto_approve=auto_approve,
            username=username,
            password=password,
        )
        self._model = model
        self._timeout = timeout

    def research(self, request: LookupRequest) -> tuple[ResearchResult, Dict[str, Any]]:
        """Run the research prompt and return (parsed result, raw AI run metadata)."""
        prompt_payload = request.to_dict()
        track_payload = prompt_payload.get("track")
        if isinstance(track_payload, dict):
            # Do not pass source-debug payloads to the AI; normalized metadata
            # and the separate credits object are sufficient for research.
            track_payload.pop("raw", None)
        prompt = build_research_prompt(prompt_payload)

        kwargs: Dict[str, Any] = {"timeout": self._timeout}
        if self._model:
            kwargs["model"] = self._model

        try:
            result = self._client.call(prompt, **kwargs)
        except Exception as exc:
            raise AIResearchError(f"AI research call failed: {exc}") from exc

        if not result.text:
            raise AIResearchError("AI research call returned no text output.")

        research_result = parse_research_response(result.text)
        meta = {
            "mode": self._client.mode,
            "model": result.model or self._model,
            "session": result.session,
            "cost": result.cost,
            "tokens": result.tokens,
        }
        return research_result, meta
