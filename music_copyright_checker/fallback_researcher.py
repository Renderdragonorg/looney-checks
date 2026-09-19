"""Failover chain across multiple AI research endpoints.

A :class:`FallbackResearcher` wraps an ordered list of researchers (primary,
then secondaries) and runs the licensing-research prompt against the first one
that succeeds. This keeps a check working when the primary provider is down,
out of credit, rate-limited, or missing configuration.

The returned ``ai_meta`` records which endpoint answered and what failed before
it, so callers can surface the failover in their UI/logs.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from .errors import AllBackendsFailedError
from .models import LookupRequest, ResearchResult


def _provider_label(researcher: Any) -> str:
    label = getattr(researcher, "provider_label", None)
    if isinstance(label, str) and label:
        return label
    return researcher.__class__.__name__


def _provider_mode(researcher: Any) -> Any:
    try:
        return researcher.mode
    except Exception:  # pragma: no cover - defensive
        return None


class FallbackResearcher:
    """Try each wrapped researcher in order until one returns a result."""

    provider_label = "AI fallback chain"

    def __init__(self, researchers: Sequence[Any]) -> None:
        researchers = [researcher for researcher in researchers if researcher is not None]
        if not researchers:
            raise ValueError("FallbackResearcher requires at least one researcher.")
        self._researchers: List[Any] = list(researchers)

    @property
    def mode(self) -> str:
        if len(self._researchers) == 1:
            return _provider_mode(self._researchers[0]) or "fallback"
        return "fallback"

    @property
    def researchers(self) -> List[Any]:
        return list(self._researchers)

    def research(self, request: LookupRequest) -> tuple[ResearchResult, Dict[str, Any]]:
        failures: List[Dict[str, Any]] = []
        for index, researcher in enumerate(self._researchers):
            label = _provider_label(researcher)
            try:
                result, meta = researcher.research(request)
            except Exception as exc:
                failures.append(
                    {
                        "provider": label,
                        "mode": _provider_mode(researcher),
                        "error": str(exc),
                    }
                )
                continue

            meta = dict(meta)
            meta.setdefault("mode", _provider_mode(researcher))
            meta["provider"] = label
            if failures:
                meta["fallback_used"] = True
                meta["fallback_attempts"] = index + 1
                meta["fallback_failures"] = failures
            else:
                meta["fallback_used"] = False
            return result, meta

        skeleton = "; ".join(f"{item['provider']}: {item['error']}" for item in failures)
        raise AllBackendsFailedError(f"All AI backends failed ({len(failures)}): {skeleton}")
