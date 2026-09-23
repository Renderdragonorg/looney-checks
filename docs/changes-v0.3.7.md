# v0.3.7 — Creator-declared licences

Minor release. New response fields; no breaking changes.

- Release: <https://github.com/Renderdragonorg/looney-checks/releases/tag/v0.3.7>
- Previous version: `0.3.6`
- Compare: <https://github.com/Renderdragonorg/looney-checks/compare/v0.3.6...v0.3.7>

---

## 1. Why

A YouTube track could be flagged `clearance_required` even when the artist had
publicly declared it royalty-free. Example: *Alohaii – Luxury (feat. vally.exe)*
has `licensed_content: true`, so the engine assumed restricted rights — but the
uploader's pinned comment reads:

> AS ALWAYS THIS ALBUM IS ROYALTY FREE TO USE IN YOUR VIDEOS, REACTIONS, OR STREAMS. CREDIT APPRECIATED!!

The YouTube source never fetched comments, so the declaration never reached the
AI researcher. Content ID registration (`licensed_content: true`) was also being
read as evidence of restriction, which it is not.

## 2. The fix

`music_copyright_checker/youtube_source.py` now makes one extra best-effort
`commentThreads.list` call (`order=relevance`, so the pinned comment comes
first) and stores up to 10 comments on `TrackMetadata.top_comments`. A helper,
`collect_license_statements()`, scans the description and comments for
free-use/licence phrases ("royalty free", "free to use", "creative commons",
"CC BY", "for credited cover/remix", ...) and stores them in
`TrackMetadata.license_statements`.

The comment fetch is best-effort: comments disabled, empty, or an API error all
yield empty lists and never fail the lookup.

`prompts.py` (`RESEARCH_PROMPT_VERSION` `3 → 6`) now:

- always includes the **full video description** in an emphasized source-text
  block (not just regex-matched lines), so the model reads it for permissions
  the creator stated in prose,
- emphasizes `license_statements`/`top_comments` in a dedicated block,
- instructs the model to record creator terms in the new
  `usage_assessment.creator_declared_license` field and to corroborate them
  against the artist's own pages,
- tells the model, for a track from an album, to also check the album's
  full-album video / Bandcamp / artist page for a blanket licence covering the
  album,
- explicitly says `licensed_content: true` is **not** proof that reuse is
  restricted,
- adds two verdict values: `free_to_use` and `permitted_with_conditions`.

The stored description cap is raised from 2000 to 4000 characters
(`MAX_DESCRIPTION_CHARS`) so long descriptions are not cut before the
licence-relevant text.

## 3. New/changed fields

`request.track` (YouTube only):

| Field | Meaning |
| --- | --- |
| `top_comments[]` | `author`, `author_channel_id`, `text`, `like_count`, `published_at`, `is_uploader` |
| `license_statements[]` | free-use/licence phrases found in the description or comments |

`research.usage_assessment`:

| Field | Meaning |
| --- | --- |
| `creator_declared_license` | the creator/rights-holder's own stated terms, or `null` |
| `video_verdict` / `social_media_verdict` / `reality_tv_verdict` | may now be `free_to_use` or `permitted_with_conditions` |

## 4. Impact

- A creator-declared royalty-free track can now come back `free_to_use` for
  online video / social media, instead of always `clearance_required`.
- The prompt-version bump re-keys the research cache once, so previously cached
  tracks re-run research on their next check.
- `top_comments` is excluded from the research cache key (like counts change
  constantly); `license_statements` is included, so a new/edited declaration
  triggers fresh research.

## 5. Also in this release

- `tests/test_youtube.py`: coverage for comment normalization, licence-statement
  extraction, the best-effort comment fetch, cache round-tripping, and prompt
  surfacing of both the description and detected declarations.
- `docs/youtube-source.md`: documents the comment call and new fields.

## 6. Upgrade notes

- Version bumped `0.3.6 → 0.3.7` (`pyproject.toml`, `__version__`).
- No configuration changes. Each YouTube video now costs one extra quota unit
  (`commentThreads.list`), so a lookup is ~2 units instead of 1.
- This remains research assistance, not legal advice.
