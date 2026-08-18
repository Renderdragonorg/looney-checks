# Server API Implementation Guide

This project exposes the copyright checker through a small JSON HTTP server.
The server owns one `Pipeline` instance and routes each request through the
same metadata extraction and Big Pickle AI research flow used by the CLI.

## Request Flow

```text
client
  -> POST /check
  -> source identification
  -> metadata and credits normalization
  -> Big Pickle research through opencode
  -> structured JSON response
```

File uploads follow one additional step:

```text
multipart upload -> temporary audio file -> Mutagen tags -> AI research -> delete temporary file
```

The upload is not retained after the response is produced.

## Caching

The server uses a persistent SQLite cache at
`~/.cache/music-copyright-checker/cache.sqlite3` by default. Set
`MUSIC_CHECKER_CACHE_PATH` or pass `--cache-path` to change it; pass
`--no-cache` to disable caching.

- Spotify metadata is keyed by canonical track ID and cached for 12 hours.
- File metadata is keyed by a streaming SHA-256 of the audio and cached for 30 days.
- Successful AI research is cached for 7 days; partial and not-found results use shorter TTLs.
- Prompt version and model are part of the AI cache key.
- Audio bytes are never stored in the cache.
- JSON requests can set `"refresh": true` to bypass existing cache entries.

Responses include cache information in `ai_meta`, including `cache_hit`,
`cache_age_seconds`, and `cached_at`. Cached research is timestamped research,
not a guarantee that licensing information remains legally current.

## 1. Install

From the project directory:

```bash
uv sync --extra dev
```

The server requires:

- Python 3.9 or newer
- the dependencies from `pyproject.toml`
- the `opencode` executable on `PATH`
- an authenticated OpenCode provider

Check the model and authentication before starting:

```bash
opencode auth list
opencode models
```

The default model is `opencode/big-pickle`. The server can receive a different
model with `--model`, but the structured response contract remains the same.

## 2. Start the Server

For local development:

```bash
uv run music-copyright-checker-server \
  --host 127.0.0.1 \
  --port 8080 \
  --model opencode/big-pickle \
  --timeout 900
```

For a server reachable from another machine on the LAN:

```bash
uv run music-copyright-checker-server \
  --host 0.0.0.0 \
  --port 8090 \
  --model opencode/big-pickle \
  --timeout 900
```

The AI request is synchronous. Keep the client request timeout longer than the
server's `--timeout` value because Spotify lookup and process startup also take
time.

The async `/jobs` queue is **off by default** — local integrations only need
`/check`. To expose the job endpoints for a proxy deployment, start the server
with `--jobs`:

```bash
uv run music-copyright-checker-server \
  --host 127.0.0.1 \
  --port 8080 \
  --jobs
```

When the API is behind Cloudflare, a reverse proxy, or another gateway with a
short request timeout, use the asynchronous job API described below instead of
holding `/check` open.

## 3. Run Persistently with systemd

For a Linux user service:

```bash
mkdir -p ~/.config/systemd/user
cp music-copyright-checker.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now music-copyright-checker.service
```

Check the service:

```bash
systemctl --user status music-copyright-checker.service
journalctl --user -u music-copyright-checker.service -f
```

The included service uses:

- `Restart=always` to recover from process failures
- `RestartSec=10` to avoid a tight restart loop
- `WorkingDirectory=%h/music_copyright_checker`
- `opencode/big-pickle`
- port `8090`

For the service to start after reboot without an interactive login, user
lingering must be enabled:

```bash
loginctl show-user "$USER" -p Linger
```

The output should contain `Linger=yes`. Enabling lingering may require an
administrator on systems where the user is not allowed to enable it.

## 4. Check Readiness and Discover the API

Readiness:

```bash
curl http://127.0.0.1:8090/health
```

Example response:

```json
{
  "status": "ok",
  "service": "music-copyright-checker",
  "ai_model": "opencode/big-pickle"
}
```

The server publishes its machine-readable integration contract:

```bash
curl http://127.0.0.1:8090/docs
```

This describes accepted request types, response fields, error statuses, and
client examples. A frontend can use it to discover the API without importing
the Python package.

## 5. Send a Spotify Request

Send exactly one `spotify_url` field as JSON:

```bash
curl -X POST http://127.0.0.1:8090/check \
  -H 'Content-Type: application/json' \
  -d '{"spotify_url":"https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"}' \
  > spotify-result.json
```

The value can also be a `spotify:track:...` URI or a bare Spotify track ID.

## 6. Send a Server-Local File

Use this form only when the audio file already exists on the machine running
the API server:

```bash
curl -X POST http://127.0.0.1:8090/check \
  -H 'Content-Type: application/json' \
  -d '{"file":"/absolute/path/on/server/song.mp3"}' \
  > file-result.json
```

The server validates the extension, reads tags with Mutagen, creates a
normalized `TrackMetadata` and `TrackCredits` request, and sends that request
to the AI researcher.

## 7. Upload a Client File

Use `multipart/form-data` when the file is on the client machine:

```bash
curl -X POST http://127.0.0.1:8090/check \
  -F 'file=@/path/to/song.mp3' \
  > upload-result.json
```

The upload flow is:

1. The server accepts the `file` form field.
2. It preserves the uploaded extension in a temporary file.
3. `FileSource.fetch()` extracts title, artist, album, date, ISRC, duration,
   bitrate, comments, and extra tags. If the title tag is empty, it falls back
   to the original filename stem.
4. `Pipeline.check_file()` creates the normalized request.
5. Big Pickle researches the track and licensing sources.
6. The response is serialized as JSON.
7. The temporary file is deleted in a `finally` block.

The filename fallback is only a search hint. The AI must corroborate it with
other metadata or authoritative sources before reporting a track identity.

The upload body limit is 100 MB. Supported extensions are `.mp3`, `.flac`,
`.m4a`, `.mp4`, `.aac`, `.ogg`, `.oga`, `.opus`, `.wav`, and `.wma`.

## 8. Download from a Direct File URL

Send a public HTTP or HTTPS audio URL as JSON:

```bash
curl -X POST https://api.example.com/jobs \
  -H 'Content-Type: application/json' \
  -d '{"file_url":"https://cdn.example.com/audio/song.mp3"}'
```

For `/jobs`, the download happens inside the background worker. The request
returns a job ID immediately, and the worker then downloads the file, extracts
metadata, searches with AI, and removes the temporary download. Use the same
SSE or polling endpoints described below.

The server requires an HTTP/HTTPS URL that resolves to a public internet
address. Localhost, private-network IPs, embedded credentials, and unsupported
schemes are rejected to prevent server-side request forgery. Redirects are
validated too. Downloads are limited to 100 MB and must provide a supported
audio extension or audio Content-Type.

## 9. Use Async Jobs Behind Cloudflare

AI research can take longer than a proxy allows for one HTTP request. Queue the
same Spotify or file request through `/jobs`:

```bash
curl -X POST https://api.example.com/jobs \
  -H 'Content-Type: application/json' \
  -d '{"spotify_url":"https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"}'
```

The server responds immediately with HTTP `202`:

```json
{
  "job_id": "8e7...",
  "status": "queued",
  "status_url": "/jobs/8e7..."
}
```

Poll the returned status URL:

```bash
curl https://api.example.com/jobs/8e7...
```

Poll until `status` is `complete` or `failed`. A completed job contains the
normal API response under `result`:

```json
{
  "job_id": "8e7...",
  "status": "complete",
  "result": {
    "request": {},
    "research": {},
    "ai_meta": {}
  }
}
```

### Stream Progress with SSE

To avoid a long, inactive spinner, connect to the job's event stream:

```javascript
const events = new EventSource(
  `https://api.example.com/jobs/${jobId}/events`,
);

events.addEventListener("progress", (event) => {
  const update = JSON.parse(event.data);
  showProgress(update.stage, update.message);

  if (update.stage === "complete" || update.stage === "failed") {
    events.close();
  }
});
```

The stream emits stages such as:

- `queued`: waiting for a worker
- `running`: job started
- `identifying_track`: Spotify metadata is being fetched
- `extracting_metadata`: uploaded file tags are being read
- `researching`: the AI is searching rights and licensing sources
- `complete`: the result is ready
- `failed`: the job stopped with an error

The SSE connection sends keep-alive comments and closes when the job reaches a
terminal state or after its short streaming window. Reconnect using the
`Last-Event-ID` header or fall back to polling `GET /jobs/{job_id}` when the
client or proxy does not support SSE.

Multipart uploads can also be queued:

```bash
curl -X POST https://api.example.com/jobs \
  -F 'file=@/path/to/song.mp3'
```

The temporary upload remains available to the background worker until the
pipeline finishes, then is deleted. Job state is held in memory, so a server
restart loses queued or running jobs and clients should resubmit them.

### Browser Upload Example

Do not set `Content-Type` manually when using `FormData`; the browser supplies
the multipart boundary:

```javascript
async function checkAudio(file) {
  const form = new FormData();
  form.append("file", file);

  const response = await fetch("http://127.0.0.1:8090/check", {
    method: "POST",
    body: form,
  });

  const result = await response.json();
  if (!response.ok) {
    throw new Error(result.error || "Copyright check failed");
  }
  return result;
}
```

## 10. Handle the Response

The successful response has three top-level fields:

```json
{
  "request": {},
  "research": {},
  "ai_meta": {}
}
```

The `research` object contains:

- `status`: `complete`, `partial`, or `not_found`
- `summary`: concise final finding
- `matches`: rights-holder, publisher, label, confidence, and evidence links
- `sources`: exact URLs and the claims they support
- `usage_assessment`: separate online-video, social-media, and reality-TV guidance
- `official_licensing_contacts`: verified licensing URLs when found
- `warnings`: uncertainty and rights-clearance caveats

Example client-side handling:

```javascript
const result = await checkAudio(file);

renderTrack(result.request.track);
renderSources(result.research.sources);
renderUsage(result.research.usage_assessment);
renderWarnings(result.research.warnings);
```

Treat `video_verdict` and `reality_tv_verdict` as research guidance, not a
legal opinion. A Content ID claim, monetization split, or platform music
library entry is not automatically permission for general use.

## 11. Error Handling

| Status | Meaning |
| --- | --- |
| `200` | Research completed and structured JSON returned. |
| `400` | Invalid JSON/multipart data or invalid source fields. |
| `413` | Request exceeds the 1 MB JSON or 100 MB upload limit. |
| `422` | Spotify lookup, file metadata extraction, or AI research failed. |
| `500` | Unexpected server error. |

Errors have this shape:

```json
{
  "error": "description of the failure"
}
```

## 12. Security and Deployment Notes

- Bind to `127.0.0.1` when only a local UI needs access.
- If binding to `0.0.0.0`, restrict port `8090` with a firewall or reverse
  proxy and add authentication before exposing it beyond a trusted LAN.
- Server-local file paths can read files accessible to the service account.
- Multipart uploads are temporary, but the server still needs enough disk space
  for the upload and enough memory for request parsing.
- Keep OpenCode credentials on the server. Do not send provider credentials from
  a browser client.

## 13. Verify an Installation

Run the local tests:

```bash
uv run pytest tests/
```

Then verify the deployed server:

```bash
curl -f http://127.0.0.1:8090/health
curl -f http://127.0.0.1:8090/docs
```

Finally upload a real audio file and confirm the response includes extracted
`request.track` metadata and `research.usage_assessment`.
