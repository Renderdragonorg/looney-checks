# RenderDragon music links

`music_copyright_checker/renderdragon_source.py` turns a
`renderdragon.org/api/music-link` URL into the same normalized
`TrackMetadata` / `TrackCredits` shape every other source produces, so the AI
research step treats it like any other track.

## What the source does

RenderDragon publishes one HTTPS endpoint that maps track metadata to a link
other apps can consume:

```
GET https://renderdragon.org/api/music-link?name=…&url=…&credits=…&category=…&ext=…&file=…&id=…
```

The endpoint answers two ways based on the `Accept` header:

| Caller sends | Response |
| --- | --- |
| `Accept: application/json` | `200` + JSON metadata |
| `Accept: text/html` (browsers) | `302` redirect to `https://renderdragon.org/resources?track=…` |

`RenderDragonSource` always sends `Accept: application/json`, so it gets data
instead of a redirect. There is no API key.

## Usage

```python
from music_copyright_checker import Pipeline

pipeline = Pipeline()

result = pipeline.check_renderdragon_url(
    "https://renderdragon.org/api/music-link"
    "?name=11%20AM&url=https%3A%2F%2Fraw.githubusercontent.com%2F...&id=123"
)
print(result.to_dict())
```

From the CLI:

```bash
python -m music_copyright_checker.cli --renderdragon-url "https://renderdragon.org/api/music-link?..." --pretty
```

From the JSON server, POST `renderdragon_url` to `/check` (or `/jobs` behind a
proxy):

```bash
curl -X POST http://127.0.0.1:8080/check \
  -H 'Content-Type: application/json' \
  -d '{"renderdragon_url":"https://renderdragon.org/api/music-link?name=Song&url=<raw-github-url>&id=12"}'
```

## Generating a link

`build_music_link()` maps a RenderDragon resource (or any mapping with the
matching fields) onto the query parameters. It prefers a direct raw GitHub URL
and rewrites a `github.com/.../blob/...` viewer URL to its raw form:

```python
from music_copyright_checker import build_music_link, parse_music_link, RenderDragonSource

link = build_music_link({
    "title": "11 AM - Animal Crossing City Folk OST",
    "download_url": "https://github.com/Yxmura/resources_renderdragon/blob/main/music/11_AM.mp3",
    "credit": "Nintendo",
    "category": "music",
    "filetype": "mp3",
    "filename": "11_AM_-_Animal_Crossing_City_Folk_OST.mp3",
    "id": 123,
})

params = parse_music_link(link)
metadata, credits = RenderDragonSource().fetch_params(params)
```

`build_music_link` refuses a `url` that is not on the allowlist, and
`RenderDragonSource.fetch_params` raises before making a request if `url` is
missing.

## Allowlisted sources

The endpoint only accepts raw files from these repos (it is not an open proxy):

- `raw.githubusercontent.com/Yxmura/resources_renderdragon/…`
- `raw.githubusercontent.com/Coder-soft/Minecraft-Creator-Safe-Playlist/…`

`is_allowlisted_source(url)` exposes the same check.

## Normalized fields

The resolved `TrackMetadata` carries:

| Music-link field | `TrackMetadata` |
| --- | --- |
| `name` | `name` |
| `credits` | `artists` (one credit string) |
| `category` | `category` |
| `id` | `renderdragon_id` |
| `direct_url` | `audio_url` |
| `website_url` | `website_url` |
| `filename` | `filename` |
| `available` | `available` |
| `size` | `size_bytes` |
| `content_type` | `content_type` |

The full response is retained under `raw`. `TrackCredits.performers` gets one
`Credit` for the credit string, and `source_note` explains that RenderDragon
does not expose ISRC or songwriter/publisher credits, so the AI fills those in.

If RenderDragon reports `available: false`, the source raises
`RenderDragonLookupError` rather than returning unverifiable metadata.

## Errors

| Exception | Cause |
| --- | --- |
| `InvalidRenderDragonLinkError` | Not a valid `renderdragon.org/api/music-link` URL, or `url` missing/not allowlisted when building. |
| `RenderDragonAPIError` | Endpoint returned `400` (not allowlisted), `422` (missing `url`), or `405` (non-GET). |
| `RenderDragonLookupError` | Network failure, invalid JSON, or `available: false`. |

All three subclass `MusicCheckerError`.

## Caching

Resolved metadata is cached by `RenderDragonSource` resource id, falling back to
the raw audio URL. `renderdragon_identity(params)` is the identity helper.
`available`, `size_bytes`, and `content_type` are treated as volatile for the
research cache key, since the host may not report them consistently (e.g. Git
LFS pointers).
