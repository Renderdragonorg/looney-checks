# YouTube Data API v3 Source

`music_copyright_checker/youtube_source.py` looks up a YouTube video through the
official **YouTube Data API v3** and normalizes it into the same
`TrackMetadata` / `TrackCredits` shape used by the Spotify and local-file
sources. The normalized request is then handed to the AI research step exactly
like any other source — see [the AI backends guide](ai-backends.md).

```text
YouTube URL / video id / search query
  -> YouTube Data API v3 (videos.list, or search.list -> videos.list)
  -> TrackMetadata + TrackCredits
  -> AI licensing research
  -> structured JSON response with request.source = "youtube"
```

---

## 1. Get an API key

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Create (or select) a project.
3. **APIs & Services → Library → YouTube Data API v3 → Enable**.
4. **APIs & Services → Credentials → Create credentials → API key**.
5. (Recommended) Restrict the key to the YouTube Data API v3.

The API has a free daily quota (default 10,000 units). Cost per call:

| Call | Units | Used for |
| --- | --- | --- |
| `videos.list` | 1 | Resolving a known video id/URL to metadata |
| `search.list` | 100 | Resolving a free-text query to a video id |

Because search costs 100× a lookup, prefer pasting a URL or video id when you
have one. Query resolutions are cached (see §6).

---

## 2. Configure the key

```bash
export YOUTUBE_API_KEY=AIza...
```

Or put it in a `.env` file (loaded automatically; see
[AI backends §5](ai-backends.md#5-configuration-via-env)):

```dotenv
YOUTUBE_API_KEY=AIza...
```

You can also pass the key directly:

```python
pipeline = Pipeline(youtube_api_key="AIza...")
```

The key is resolved lazily: constructing `Pipeline`/`YouTubeSource` never
requires it, but an actual YouTube request raises a clear
`YouTubeLookupError` if no key is configured.

---

## 3. Accepted inputs

`Pipeline.check_youtube_url(...)`, the CLI `--youtube-url`, and the server
`youtube_url` field all accept:

| Input | Example |
| --- | --- |
| Watch URL | `https://www.youtube.com/watch?v=dQw4w9WgXcQ` |
| Short URL | `https://youtu.be/dQw4w9WgXcQ?si=abc` |
| Shorts | `https://www.youtube.com/shorts/dQw4w9WgXcQ` |
| Embed / live / v | `https://www.youtube.com/embed/dQw4w9WgXcQ`, `/live/…`, `/v/…` |
| YouTube Music | `https://music.youtube.com/watch?v=dQw4w9WgXcQ` |
| Bare video id | `dQw4w9WgXcQ` (exactly 11 chars) |
| Search query | `Rick Astley - Never Gonna Give You Up` |

A search query is resolved with `search.list` (`type=video`,
`videoCategoryId=10` "Music", `maxResults=1`). If the Music category returns
nothing, it retries without the category filter.

When a controller should choose the video, use `search_videos()` /
`POST /youtube/search` instead: it returns up to 5 candidates (video id, URL,
title, channel, description, published date, thumbnail) and the chosen id/URL is
passed back to the normal check.

---

## 4. What gets normalized

`normalize_video_info()` maps a `videos.list` item onto `TrackMetadata`:

| `TrackMetadata` field | Source |
| --- | --- |
| `name` | `snippet.title` |
| `artists` | `[snippet.channelTitle]` |
| `channel` / `channel_id` / `channel_url` | `snippet.channelTitle` / `channelId` |
| `description` | `snippet.description` (truncated to 2000 chars) |
| `tags` | `snippet.tags` |
| `category` | `topicDetails` "Music", else mapped `snippet.categoryId` |
| `release_date` | `snippet.publishedAt` |
| `duration_ms` | `contentDetails.duration` (ISO-8601 → ms) |
| `licensed_content` | `contentDetails.licensedContent` |
| `published_at` | `snippet.publishedAt` |
| `view_count` | `statistics.viewCount` |
| `thumbnail_url` | best `snippet.thumbnails` size |
| `youtube_id` / `youtube_url` | requested video id |

### Credits

The YouTube Data API does not expose ISRCs or songwriter/publisher credits, so
`build_youtube_credits()` reports only what YouTube does provide:

- `performers`: the channel/uploader, role `"Uploader"`.
- `source_label`: the label parsed from a `Provided to YouTube by <label>` line
  in the description, when present.
- `source_note`: explains that ISRC/songwriter data is unavailable from YouTube
  and is filled in by the AI research step.

`licensed_content: true` means the upload was matched by YouTube's content
system — **it is not a licence** for reuse. The AI research step still performs
the real rights research.

---

## 5. Usage

### Python

```python
from music_copyright_checker import Pipeline

pipeline = Pipeline()  # OpenRouter backend; YOUTUBE_API_KEY from env/.env
result = pipeline.check_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
print(result.to_dict())

# Or search by name
result = pipeline.check_youtube_url("Rick Astley - Never Gonna Give You Up")

# Let a controller choose: candidates include titles and thumbnail_url
candidates = pipeline.search_youtube("Rick Astley - Never Gonna Give You Up", limit=5)
chosen = candidates[0]["video_id"]
result = pipeline.check_youtube_url(chosen)
```

`YouTubeSource.search_videos(query, limit=5)` is the lower-level equivalent;
`search_video(query)` still auto-picks the first result.

### CLI

```bash
music-copyright-checker --youtube-url https://www.youtube.com/watch?v=dQw4w9WgXcQ --pretty
music-copyright-checker --youtube-url "Rick Astley - Never Gonna Give You Up" --pretty
music-copyright-checker --youtube-url dQw4w9WgXcQ --no-ai --pretty   # metadata only
```

### JSON server

```bash
curl -X POST http://127.0.0.1:8080/check \
  -H 'Content-Type: application/json' \
  -d '{"youtube_url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'
```

The response uses the same shape as the Spotify path, with
`request.source = "youtube"`:

```json
{
  "request": {
    "source": "youtube",
    "input": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "track": {
      "name": "Rick Astley - Never Gonna Give You Up (Official Video)",
      "artists": ["Rick Astley"],
      "youtube_id": "dQw4w9WgXcQ",
      "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
      "channel": "Rick Astley",
      "category": "Music",
      "licensed_content": true,
      "duration_ms": 214000,
      "view_count": 1815619726
    },
    "credits": {
      "available": true,
      "performers": [{ "name": "Rick Astley", "role": "Uploader" }]
    }
  },
  "research": { "status": "complete", "matches": [], "sources": [] },
  "ai_meta": { "mode": "openrouter", "model": "openrouter/free" }
}
```

---

## 6. Caching and identity

- Video metadata: `metadata:v1:youtube:<video_id>`, cached 12 hours.
- Query resolution: `metadata:v1:youtube-query:<normalized query>`, cached
  12 hours — so repeating a search does not spend another 100 quota units.
- The AI research cache identity prefers ISRC, then Spotify id, then YouTube
  video id, so the same video is deduplicated across URL/query forms.
- Use `refresh=true` (JSON) or `--refresh` (CLI) to bypass cached values.

---

## 7. Notes, quota and errors

- **SSL:** the client uses `certifi`'s CA bundle, so it works on python.org
  macOS builds and in frozen binaries.
- **Retries:** transient TLS/network drops are retried up to 3 times.
- **Duration:** parsed from ISO-8601 (`PT3M33S`); live placeholders like `P0D`
  become `0`.
- **Quota exhaustion** returns a `YouTubeAPIError`; the default free daily quota
  is 10,000 units (≈10,000 lookups or ≈100 searches).

Error classes (all subclass `MusicCheckerError`, so the server returns HTTP 422):

| Error | Meaning |
| --- | --- |
| `InvalidYouTubeURLError` | Input was not a recognizable URL/video id |
| `YouTubeLookupError` | Network failure, or video missing/private/unavailable, or no search result |
| `YouTubeAPIError` | The API returned an error (invalid key, quota, etc.) |

---

## 8. Package API

```python
from music_copyright_checker.youtube_source import (
    YouTubeSource,
    parse_video_id,
    normalize_video_info,
    build_youtube_credits,
    iso8601_duration_to_ms,
    resolve_api_key,
)
```

- `parse_video_id(value) -> str` — extract the 11-char id from any supported form.
- `normalize_search_item(item) -> dict | None` — shape one `search.list` item into a
  candidate (`video_id`, `url`, `title`, `channel`, `channel_id`, `description`,
  `published_at`, `thumbnail_url`).
- `YouTubeSource(api_key=None, timeout=30.0)` with
  `.resolve_video_id(value)`, `.search_videos(query, limit=5)`, `.search_video(query)`,
  `.fetch_video(id)`, `.fetch(value)`.
