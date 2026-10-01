# v0.4.0 — RenderDragon music links

Minor release. New track source plus new response fields; no breaking changes.

- Release: <https://github.com/Renderdragonorg/looney-checks/releases/tag/v0.4.0>
- Previous version: `0.3.7`
- Compare: <https://github.com/Renderdragonorg/looney-checks/compare/v0.3.7...v0.4.0>

---

## 1. Why

RenderDragon maps track metadata to a single HTTPS link
(`https://renderdragon.org/api/music-link?...`) that other apps can resolve.
Callers embedding this package had to resolve those links themselves, then
re-shape the JSON into the pipeline's `TrackMetadata` before a copyright check
could run.

This release makes a RenderDragon music link a first-class source: paste the
link, get the same research result as Spotify, YouTube, or a local file.

## 2. What's new

`music_copyright_checker/renderdragon_source.py`:

- `RenderDragonSource.fetch(link)` / `.fetch_params(params)` — GETs the
  endpoint with `Accept: application/json` (never the browser redirect), retries
  transient TLS/network drops, and maps `400` (not allowlisted), `422` (missing
  `url`), and `405` (non-GET) to `RenderDragonAPIError`.
- `parse_music_link(link)` — validates the `renderdragon.org/api/music-link`
  host/path and returns the decoded query parameters.
- `build_music_link(track)` — maps renderdragon resource fields (`title`,
  `download_url`, `credit`, `category`, `filetype`, `filename`, `id`) onto a
  music link, rewrites a GitHub `blob` viewer URL to its raw form, and refuses
  a `url` that is not in the allowlist.
- `to_raw_github_url()`, `is_allowlisted_source()`, `renderdragon_identity()`,
  and `normalize_music_link()` helpers.

Only raw files from `raw.githubusercontent.com/Yxmura/resources_renderdragon/…`
and `raw.githubusercontent.com/Coder-soft/Minecraft-Creator-Safe-Playlist/…`
are accepted, matching the endpoint's own allowlist. If RenderDragon reports
`available: false`, the source raises `RenderDragonLookupError` instead of
returning unverifiable metadata.

Wiring:

- `Pipeline.check_renderdragon_url(link, progress=…, refresh=…)`, with metadata
  cached by RenderDragon resource id (falling back to the raw audio URL).
- CLI: `--renderdragon-url`.
- JSON server: `renderdragon_url` on `/check` (and `/jobs`), plus a
  `renderdragon_json` request mode and curl example in `/docs`.
- New exceptions `InvalidRenderDragonLinkError`, `RenderDragonLookupError`, and
  `RenderDragonAPIError` (all subclass `MusicCheckerError`).

## 3. New/changed fields

`request.track` (RenderDragon source only):

| Field | Meaning |
| --- | --- |
| `renderdragon_id` | RenderDragon resource id from the link's `id` parameter |
| `audio_url` | canonical raw audio URL (`direct_url`) |
| `website_url` | human-facing track page (`website_url`) |
| `filename` | original audio filename |
| `available` | whether the endpoint verified the file exists |
| `size_bytes` | reported file size, or `null` (e.g. Git LFS pointers) |
| `content_type` | reported MIME type, or `null` |

The full endpoint response is retained under `raw`.

## 4. Usage

```python
from music_copyright_checker import Pipeline, build_music_link

pipeline = Pipeline()

link = build_music_link({
    "title": "11 AM - Animal Crossing City Folk OST",
    "download_url": "https://github.com/Yxmura/resources_renderdragon/blob/main/music/11_AM.mp3",
    "credit": "Nintendo",
    "category": "music",
    "filetype": "mp3",
    "filename": "11_AM.mp3",
    "id": 123,
})

print(pipeline.check_renderdragon_url(link).to_dict())
```

```bash
# CLI
music-copyright-checker --renderdragon-url "https://renderdragon.org/api/music-link?..." --pretty

# Server
curl -X POST http://127.0.0.1:8080/check \
  -H 'Content-Type: application/json' \
  -d '{"renderdragon_url":"https://renderdragon.org/api/music-link?name=Song&url=<raw-github-url>&id=12"}'
```

## 5. Impact

- Metadata is cached by `renderdragon_id` (or the raw audio URL), so the same
  link is resolved once.
- `research_identity()` now recognizes `renderdragon_id`, and `available`,
  `size_bytes`, and `content_type` are excluded from the research cache key
  because the host may not report them consistently.
- No breaking changes: existing sources, request fields, and result shape are
  unchanged.

## 6. Also in this release

- `tests/test_renderdragon.py`: link parsing, link building, allowlist
  enforcement, normalization, error mapping, identity, and pipeline caching.
- `tests/test_server.py`: coverage for the `renderdragon_url` request mode.
- `docs/renderdragon-source.md`: the full source guide.

## 7. Upgrade notes

- Version bumped `0.3.7 → 0.4.0` (`pyproject.toml`, `__version__`).
- No configuration changes and no API keys required for this source.
- This remains research assistance, not legal advice.
