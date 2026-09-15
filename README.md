# music-copyright-checker

A UI-free backend package: give it a Spotify track URL (or a local audio
file), it gathers whatever metadata/credits it can, hands that to an AI
agent to research official licensing across the web, and returns everything
as one JSON object. Meant to be the engine behind a separate Tauri UI later —
nothing here renders anything.

## Pipeline

```
Spotify URL ──┐
              ├─► normalize metadata/credits ─► JSON ─► AI research (opencode) ─► JSON result
Audio file ───┘
```

1. **Source**
   - `spotify_source.py` — parses a `open.spotify.com/track/...` URL, a
     `spotify:track:...` URI, or a bare id, then uses
     [SpotAPI](https://github.com/Aran404/SpotAPI) (`spotapi.Song.get_track_info`)
     to pull name/artists/album/ISRC/etc. It also makes a best-effort attempt
     at Spotify's undocumented track-credits data; when that's unavailable
     (it often will be — it's an unofficial, unstable endpoint) it falls back
     to treating the listed artists as "performers" and leaves songwriter/
     producer credits for the AI step to find from public sources.
   - `file_source.py` — reads a local audio file's tags (MP3/FLAC/M4A/OGG/WAV/...)
     via `mutagen`, including format-specific ISRC frames (e.g. ID3 `TSRC`).

2. **Normalize** — both sources are folded into the same `LookupRequest` /
   `TrackMetadata` / `TrackCredits` shape (see `models.py`), so the AI step
   doesn't need to know where the data came from.

3. **AI research** — `prompts.py` builds a prompt that hands the AI the
   normalized JSON and asks it to search multiple sources (MusicBrainz,
   ASCAP/BMI/PRS, label/publisher sites, Discogs, ...) for the real rights
   holder and how to license the track, then reply with strict JSON. By default
   this runs through `openrouter_researcher.py`, which calls the OpenRouter REST
   API (`openrouter_client.py`) with the free model router (`openrouter/free`)
   and OpenRouter's server-side `openrouter:web_search` tool for live research —
   no local agent/browser process. Set `OPENROUTER_API_KEY` to enable it.
   `ai_backend="opencode"` still drives the bundled
   [`opencode-harness`](docs/opencode-harness/) CLI agent as an alternative.
   The response is parsed back into a `ResearchResult`.

4. **Result** — `pipeline.py`'s `Pipeline.check_spotify_url()` /
   `.check_youtube_url()` / `.check_file()` return a `CopyrightCheckResult`
   with `.to_dict()` giving you the full JSON: original request, normalized
   metadata/credits, and the AI's research findings (matches, confidence,
   licensing contacts, warnings).

## Download a prebuilt binary

Each GitHub Release ships a self-contained **onedir** archive for every major
platform — no Python, no `pip`, no build step. Extract it and run the launcher
inside:

| Platform | Asset |
| --- | --- |
| Linux x86_64 | `music-copyright-checker-<version>-linux-x86_64.tar.gz` |
| Linux arm64 | `music-copyright-checker-<version>-linux-aarch64.tar.gz` |
| macOS (Intel) | `music-copyright-checker-<version>-macos-x86_64.tar.gz` |
| macOS (Apple Silicon) | `music-copyright-checker-<version>-macos-aarch64.tar.gz` |
| Windows x86_64 | `music-copyright-checker-<version>-windows-x86_64.zip` |

```bash
tar -xzf music-copyright-checker-<version>-<target>.tar.gz
cd music-copyright-checker

# CLI
./music-copyright-checker --spotify-url https://open.spotify.com/track/xxxx --pretty
./music-copyright-checker --youtube-url https://www.youtube.com/watch?v=xxxxxxxxxxx --pretty
./music-copyright-checker --file ./song.mp3 --pretty

# Local JSON server for integrating into other apps (no /jobs endpoint)
./music-copyright-checker server --host 127.0.0.1 --port 8080
```

On Windows, unzip the archive and run `music-copyright-checker\music-copyright-checker.exe`.

The archive is a directory bundle (not a single file) on purpose: a onefile
build re-extracts its whole bundle to a temp directory on **every** launch,
which makes warm startup slow (~17s on macOS). The onedir launcher starts in
well under a second.

The default AI backend is the OpenRouter REST API, so the only thing you need
is an OpenRouter key: export `OPENROUTER_API_KEY` (or pass `--ai-backend
opencode` to use the `opencode` CLI agent instead, which on first run is
downloaded via the official installer into `~/.opencode/bin`).

Checksums are published next to the archives in the release (`SHA256SUMS`).

For everything else — backend/model flags, the full flag reference, embedding
the local JSON server into another app, building from source, and the
CI/release pipeline — see [the binaries user guide](docs/binaries.md).

## Install

```bash
pip install -e .     # or: uv pip install -e .
```

The default AI backend calls OpenRouter directly and needs `OPENROUTER_API_KEY`.
The `opencode_harness` package is vendored for the optional
`--ai-backend opencode` path; using it requires the `opencode` CLI binary on
`PATH` (checked at `AIResearcher` construction), or a running `opencode serve`
via `--opencode-server`.

## Caching

The pipeline persistently caches Spotify metadata, local-file metadata, and the
structured AI research result in SQLite. The default cache is
`~/.cache/music-copyright-checker/cache.sqlite3`; configure another location with
`MUSIC_CHECKER_CACHE_PATH`, `--cache-path`, or disable it with `--no-cache`.

Spotify URLs are keyed by their canonical track ID and YouTube videos by their
11-character video ID (free-text YouTube queries cache their resolved video ID
too). Audio files are identified by a streaming SHA-256 hash, so temporary
upload paths and renamed files do not cause repeated AI research. Audio bytes
are never stored in the cache.

Cached research is versioned by the prompt and model, expires after seven days
by default, and reports `ai_meta.cache_hit`, `ai_meta.cache_age_seconds`, and
`ai_meta.cached_at`. Send `{"refresh":true}` to a JSON API request or use the
CLI `--refresh` flag when a fresh lookup is required. Cached research is a
timestamped research result, not a guarantee that licensing information remains
legally current.

## Usage

```python
from music_copyright_checker import Pipeline

pipeline = Pipeline()  # OpenRouter backend, model openrouter/free (needs OPENROUTER_API_KEY)

result = pipeline.check_spotify_url("https://open.spotify.com/track/6rqhFgbbKwnb9MLmUQDhG6")
print(result.to_dict())

# YouTube Data API v3: paste a URL, a bare 11-char video id, or a search query.
# Set YOUTUBE_API_KEY (e.g. in your .env); required for the YouTube source.
result_yt = pipeline.check_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
print(result_yt.to_dict())

# Or search first and let the caller pick from up to 5 candidates (with thumbnails).
for candidate in pipeline.search_youtube("Rick Astley - Never Gonna Give You Up", limit=5):
    print(candidate["title"], candidate["thumbnail_url"], candidate["url"])

result2 = pipeline.check_file("/path/to/song.mp3")
print(result2.to_dict())
```

Or from the command line:

```bash
python -m music_copyright_checker.cli --spotify-url https://open.spotify.com/track/6rqhFgbbKwnb9MLmUQDhG6 --pretty
python -m music_copyright_checker.cli --youtube-url https://www.youtube.com/watch?v=dQw4w9WgXcQ --pretty
python -m music_copyright_checker.cli --youtube-url "Rick Astley - Never Gonna Give You Up" --pretty
python -m music_copyright_checker.cli --file ./song.mp3 --pretty
python -m music_copyright_checker.cli --spotify-url spotify:track:xxxx --no-ai   # skip AI, just inspect normalized metadata
python -m music_copyright_checker.cli --spotify-url spotify:track:xxxx --model openrouter/free --pretty
```

## Documentation

- [AI backends](docs/ai-backends.md) — OpenRouter (default) and opencode, keys, models, `.env`.
- [YouTube Data API v3 source](docs/youtube-source.md) — key setup, accepted inputs, normalized fields, quota.
- [Server API guide](docs/server-api.md) — `/check`, `/youtube/search`, `/jobs`, `/docs`, deployment.
- [Downloadable binaries](docs/binaries.md) — prebuilt binaries, CLI flags, CI/release.
- [v0.3.0 changes and migration](docs/changes-v0.3.0.md) — what changed, upgrading from 0.2.x.

## JSON server

For complete installation, API, upload, client-integration, and deployment
steps, see [the server API implementation guide](docs/server-api.md).

Start the server on localhost. The async `/jobs` queue is off by default —
local client integrations only need the synchronous `/check` endpoint; add
`--jobs` for proxy/Cloudflare deployments:

```bash
uv run music-copyright-checker-server --host 127.0.0.1 --port 8080
uv run music-copyright-checker-server --host 127.0.0.1 --port 8080 --jobs  # proxy deployments
```

For a persistent Linux user service, install
`music-copyright-checker.service` under `~/.config/systemd/user/`, then run:

```bash
systemctl --user daemon-reload
systemctl --user enable --now music-copyright-checker.service
systemctl --user status music-copyright-checker.service
```

Check readiness:

```bash
curl http://127.0.0.1:8080/health
```

Read the complete integration contract and implementation steps from the running server:

```bash
curl http://127.0.0.1:8080/docs
```

Send a Spotify request. The response is the same structured JSON returned by
`Pipeline`:

```bash
curl -X POST http://127.0.0.1:8080/check \
  -H 'Content-Type: application/json' \
  -d '{"spotify_url":"https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"}' \
  > results/server-result.json
```

Let the caller choose a YouTube video by searching first: `/youtube/search`
returns up to five candidates with thumbnails, then send the chosen
`video_id`/`url` to `/check`:

```bash
curl -X POST http://127.0.0.1:8080/youtube/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"Rick Astley - Never Gonna Give You Up","limit":5}'
```

When the server is behind Cloudflare or another proxy, use the asynchronous
job route so the AI research does not exceed the proxy request timeout:

```bash
curl -X POST http://127.0.0.1:8080/jobs \
  -H 'Content-Type: application/json' \
  -d '{"spotify_url":"https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"}'

curl http://127.0.0.1:8080/jobs/JOB_ID
```

For live progress, connect to the job's SSE stream:

```javascript
const events = new EventSource(`/jobs/${jobId}/events`);
events.addEventListener("progress", (event) => {
  const update = JSON.parse(event.data);
  showProgress(update.stage, update.message);
});
```

Local audio files can be routed with `{"file":"/absolute/path/to/song.mp3"}`.
File paths are read by the server process, so keep the server bound to localhost
unless the deployment explicitly protects this endpoint.

For a file on the client machine, upload it as multipart form data. The server
temporarily stores the upload, extracts its tags, sends the normalized metadata
to the AI, returns the result, and deletes the temporary file:

```bash
curl -X POST http://127.0.0.1:8080/check \
  -F 'file=@/path/to/song.mp3' \
  > results/upload-result.json
```

If the file has no title tag, the original filename stem is used as a search
hint. The AI must corroborate that hint before treating the track as identified;
an arbitrary filename is never treated as proof of identity.

Direct public audio URLs are also supported through the async API:

```bash
curl -X POST http://127.0.0.1:8080/jobs \
  -H 'Content-Type: application/json' \
  -d '{"file_url":"https://cdn.example.com/song.mp3"}'
```

Only public HTTP/HTTPS URLs are accepted, with a 100 MB download limit.

### Result shape

```json
{
  "request": {
    "source": "spotify",
    "input": "https://open.spotify.com/track/...",
    "track": { "name": "...", "artists": ["..."], "album": "...", "isrc": "...", "...": "..." },
    "credits": { "available": true, "performers": [...], "songwriters": [...], "producers": [...] }
  },
  "research": {
    "status": "complete",
    "summary": "...",
    "matches": [
      {
        "source_name": "MusicBrainz",
        "source_url": "https://...",
        "confidence": "high",
        "rights_holder": "...",
        "publisher": "...",
        "label": "...",
        "license_type": "...",
        "notes": "..."
      }
    ],
    "sources": [
      {
        "name": "MusicBrainz",
        "url": "https://...",
        "source_type": "database",
        "supports": "track identity and credits"
      }
    ],
    "usage_assessment": {
      "video_verdict": "clearance_required",
      "social_media_verdict": "clearance_required",
      "reality_tv_verdict": "clearance_required",
      "sync_license_required": true,
      "master_license_required": true,
      "platform_exception": "A platform claim or creator-library license is not general permission.",
      "caveats": ["Clear both composition and master rights."]
    },
    "official_licensing_contacts": ["https://..."],
    "warnings": ["..."]
  },
  "ai_meta": { "mode": "process", "model": "...", "cost": 0.01, "tokens": {...}, "session": null }
}
```

## Package layout

```
music_copyright_checker/
├── __init__.py        # public exports: Pipeline, models, errors
├── models.py           # dataclasses: TrackMetadata, TrackCredits, LookupRequest,
│                        #              LicenseMatch, ResearchResult, CopyrightCheckResult
├── spotify_source.py    # Spotify URL/URI parsing + SpotAPI lookup + normalization
├── youtube_source.py    # YouTube Data API v3 video lookup + search + normalization
├── file_source.py       # local audio file tag extraction (mutagen)
├── prompts.py            # the licensing-research prompt template
├── openrouter_client.py  # direct OpenRouter REST client (free router + web_search tool)
├── openrouter_researcher.py  # OpenRouter backend (prompt -> research + parsing)
├── ai_researcher.py      # optional opencode-harness backend + response parsing
├── pipeline.py            # Pipeline: wires sources + AI backend together
├── bootstrap.py           # downloads/installs the opencode CLI when missing
├── cli.py                  # thin CLI wrapper, no UI
├── server.py               # JSON HTTP API around Pipeline (/jobs opt-in with --jobs)
├── entrypoints.py          # dispatcher behind the packaged binaries (CLI + server)
└── errors.py                # exception hierarchy
opencode_harness/            # vendored opencode-harness package (drives the opencode CLI/serve API)
packaging/
├── music_copyright_checker.spec   # PyInstaller spec (one binary per platform)
└── smoke_test.py                   # CI smoke test for built binaries
tests/
└── test_basic.py             # dependency-free unit tests (URL parsing, normalization, JSON parsing)
docs/opencode-harness/         # reference docs for the vendored opencode-harness API
```

## Notes / known limits

- SpotAPI is an **unofficial** wrapper around Spotify's private web
  endpoints; it can break when Spotify changes internal APIs (see its GitHub
  issues). `SpotifyLookupError` wraps any failure so callers can surface a
  clean error instead of a raw traceback.
- SpotAPI's documented `get_track_info()` method returns track metadata, not
  songwriter/producer credits. `_try_fetch_spotify_credits()` makes a
  best-effort request to Spotify's undocumented private credits endpoint using
  SpotAPI's session; that endpoint can be unavailable, reshaped, blocked, or
  absent from the installed SpotAPI session. The `source_note` explains this
  fallback, and the AI research step fills the gap from public sources.
- A track being playable, unclaimed, monetized through Content ID, or available
  in a creator music library does not automatically grant general video or
  reality-TV permission. The usage assessment distinguishes those platform
  mechanisms from composition/sync and master recording clearance.
- The AI is explicitly instructed to only report what it actually found
  while searching, and to flag disagreement between sources in `warnings`
  rather than silently picking one. Treat `matches[].confidence` as a signal,
  not a guarantee — this is research assistance, not a legal opinion.
