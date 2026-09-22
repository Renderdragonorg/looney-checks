# v0.3.5 — Same-backend retries before AI fallback

Patch release. No CLI, API, or response-shape changes.

- Release: <https://github.com/Renderdragonorg/looney-checks/releases/tag/v0.3.5>
- Previous version: `0.3.4`
- Compare: <https://github.com/Renderdragonorg/looney-checks/compare/v0.3.4...v0.3.5>

---

## 1. Why

`FallbackResearcher` advanced to the next backend on **any** exception. Free
routers are flaky in a way that is not a hard failure: they return an empty
completion, or text with no parseable JSON object. Those were treated as
failures and immediately shadowed by the fallback provider, so a transient
primary hiccup was silently answered by the secondary instead of being re-run.

Observed on the primary `openrouter` (`openrouter/free`) with a Token Harbor
fallback — the primary returned unparseable text and the chain advanced:

```json
{
  "provider": "OpenAI-compatible",
  "fallback_used": true,
  "fallback_attempts": 2,
  "fallback_failures": [
    {
      "provider": "OpenRouter",
      "transient": false,
      "error": "Could not find a valid JSON object in the AI's response. Raw response started with: '{\"status\":\"complete\",...'"
    }
  ]
}
```

---

## 2. The fix

`music_copyright_checker/fallback_researcher.py` now retries a *transient*
failure on the **same** endpoint before advancing the chain:

- `AIResponseParseError` (no/unparseable JSON object) is transient by type.
- Message markers also cover an empty completion, invalid/truncated JSON,
  `429`/`5xx`, and dropped sockets.
- Hard failures — bad input, a missing key, a timeout — still advance
  immediately, so a genuinely broken primary does not stall the check.

Retries per backend default to `1` (one extra attempt) and are configurable via
`MUSIC_CHECKER_FALLBACK_RETRIES` (`0` disables). The retry backoff is 2 seconds.

`ai_meta` now separates the two cases: same-backend failures are reported under
`retry_failures`, while `fallback_used` / `fallback_attempts` /
`fallback_failures` are reserved for a *different* backend answering.

---

## 3. Impact

- An OpenRouter empty completion or parse failure re-runs **OpenRouter**; Token
  Harbor is only used once the retry budget is exhausted.
- Successful single-backend runs are unchanged.
- A transient failure costs one extra attempt plus a 2-second backoff.

---

## 4. Also in this release

- `tests/test_fallback.py`: regressions for same-backend retry on an empty
  completion and on `AIResponseParseError`, plus non-transient and
  retries-disabled cases.

## 5. Upgrade notes

- Version bumped `0.3.4 → 0.3.5` (`pyproject.toml`, `__version__`).
- New optional env var `MUSIC_CHECKER_FALLBACK_RETRIES` (default `1`).
