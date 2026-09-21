# v0.3.4 — Durable YouTube Research Caching

Patch release. No CLI, API, or response-shape changes.

- Release: <https://github.com/Renderdragonorg/looney-checks/releases/tag/v0.3.4>
- Previous version: `0.3.3`
- Compare: <https://github.com/Renderdragonorg/looney-checks/compare/v0.3.3...v0.3.4>

---

## 1. Why

The research cache key hashed the **entire** request payload, including
per-fetch volatile YouTube fields. `TrackMetadata` carries `view_count`,
`description`, `tags`, `thumbnail_url`, `external_ids`, and the source-debug
`raw` payload, and `view_count` in particular changes continuously. The same
video therefore produced a different cache key on almost every check, so
YouTube checks never reused research and paid for a fresh AI run each time.
Spotify has none of those fields, which is why it cached correctly.

Observed before the fix — same video id, different view count, different key:

```text
youtube:9bzkp7q19f0 ... view=1000 -> ...:bfb609c3...
youtube:9bzkp7q19f0 ... view=1001 -> ...:b68ebda4...
same? False
```

---

## 2. The fix

`music_copyright_checker/cache.py` now drops volatile, identity-free fields
before hashing the payload:

```python
_VOLATILE_TRACK_FIELDS = frozenset(
    {"view_count", "raw", "description", "tags", "thumbnail_url", "external_ids"}
)
```

`research_cache_key()` removes those from the payload's `track` object before
computing `payload_hash`. The stable recording identity is unchanged: it still
comes from `research_identity()` (ISRC → Spotify id → YouTube id → content
hash), and the prompt version and model chain remain part of the key. Two
different videos, or two different recordings, still get different keys.

Spotify keys are byte-identical to before (those fields are absent, so nothing
is removed). Only YouTube keys change, once.

---

## 3. Impact

- YouTube research now stays cached for its full TTL (7 days complete, 24 hours
  partial, 6 hours not-found) regardless of view count, description, or tag
  churn.
- First check after upgrading re-runs research once per video, then hits.
- Cross-source (Spotify vs. YouTube) and per-model-chain keys still differ by
  design; a fallback result is never served as the primary's.

---

## 4. Also in this release

- `tests/test_cache.py`: regressions covering volatile-field stability and
  recording-identity distinction.

## 5. Upgrade notes

- Version bumped `0.3.3 → 0.3.4` (`pyproject.toml`, `__version__`).
- No configuration changes. Existing Spotify cache entries keep working;
  YouTube entries are re-keyed once.
