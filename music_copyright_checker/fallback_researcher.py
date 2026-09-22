"""Failover chain across multiple AI research endpoints.

A :class:`FallbackResearcher` wraps an ordered list of researchers (primary,
then secondaries) and runs the licensing-research prompt against the first one
that succeeds. This keeps a check working when the primary provider is down,
out of credit, rate-limited, or missing configuration.

A *transient* failure (an empty completion, truncated/invalid JSON, a rate
limit, a 5xx, a dropped socket) is retried on the **same** endpoint before the
chain advances, so a flaky free router is re-run rather than being shadowed by
a fallback provider. Hard failures (bad input, missing key, timeout) move on
immediately.

The returned ``ai_meta`` records which endpoint answered and what failed before
it, so callers can surface the failover in their UI/logs.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Sequence

from .errors import AIResponseParseError, AllBackendsFailedError
from .models import LookupRequest, ResearchResult

# Substrings that mark a failure as worth retrying on the same endpoint.
_TRANSIENT_MARKERS = (
    "empty completion",
    "returned no text",
    "invalid json",
    "could not find a valid json",
    "could not parse",
    "truncated",
    "unexpected end of json",
    "expecting value",
    "rate limit",
    "too many requests",
    "429",
    "500",
    "502",
    "503",
    "504",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "connection reset",
    "connection aborted",
    "connection refused",
    "broken pipe",
    "server disconnected",
    "remotedisconnected",
)

DEFAULT_RETRIES_ENV = "MUSIC_CHECKER_FALLBACK_RETRIES"
DEFAULT_RETRY_DELAY_SECONDS = 2.0


def _is_transient(exc: BaseException) -> bool:
    # Unparseable model output (no JSON object) is a provider flake: rerun the
    # same backend rather than advancing the chain.
    if isinstance(exc, AIResponseParseError):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_MARKERS)


def _env_retries() -> int:
    raw = os.environ.get(DEFAULT_RETRIES_ENV)
    if raw is None or not raw.strip():
        return 1
    try:
        value = int(raw)
    except ValueError:
        return 1
    return max(0, value)


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

    def __init__(
        self,
        researchers: Sequence[Any],
        *,
        retries_per_backend: int | None = None,
        retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
    ) -> None:
        researchers = [researcher for researcher in researchers if researcher is not None]
        if not researchers:
            raise ValueError("FallbackResearcher requires at least one researcher.")
        self._researchers: List[Any] = list(researchers)
        self._retries = _env_retries() if retries_per_backend is None else max(0, int(retries_per_backend))
        self._retry_delay = max(0.0, float(retry_delay_seconds))

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
        attempts_per_backend = self._retries + 1
        for index, researcher in enumerate(self._researchers):
            label = _provider_label(researcher)
            for attempt in range(attempts_per_backend):
                try:
                    result, meta = researcher.research(request)
                except Exception as exc:
                    failures.append(
                        {
                            "provider": label,
                            "mode": _provider_mode(researcher),
                            "attempt": attempt + 1,
                            "transient": _is_transient(exc),
                            "error": str(exc),
                        }
                    )
                    # Re-run a transient failure on this same backend; a hard
                    # failure (or an exhausted budget) advances the chain.
                    if _is_transient(exc) and attempt + 1 < attempts_per_backend:
                        if self._retry_delay:
                            time.sleep(self._retry_delay)
                        continue
                    break

                meta = dict(meta)
                meta.setdefault("mode", _provider_mode(researcher))
                meta["provider"] = label
                # `fallback_used` means a *different* backend answered; a
                # transient retry on the primary is reported separately.
                if index > 0:
                    meta["fallback_used"] = True
                    meta["fallback_attempts"] = index + 1
                    meta["fallback_failures"] = failures
                else:
                    meta["fallback_used"] = False
                    if failures:
                        meta["retry_failures"] = failures
                return result, meta

        skeleton = "; ".join(f"{item['provider']}: {item['error']}" for item in failures)
        raise AllBackendsFailedError(f"All AI backends failed ({len(failures)}): {skeleton}")
