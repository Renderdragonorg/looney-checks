# Downloadable Binaries — User Guide

The project ships prebuilt, self-contained binaries for every major platform.
A single binary replaces the Python package: it bundles the app, its Python
dependencies, and the vendored opencode-harness driver, and on first run it
downloads the one remaining runtime dependency — the `opencode` CLI — using
the official installer. No Python, no `pip`, no virtualenv, no build step.

This guide covers downloading, first-run setup, the CLI, the local JSON
server, embedding into another application, and how the binaries are built
and published.

---

## 1. What you download

Binaries are published as GitHub Release assets. One file per platform:

| Platform | Asset name |
| --- | --- |
| Linux x86_64 | `music-copyright-checker-<version>-linux-x86_64` |
| Linux arm64 (aarch64) | `music-copyright-checker-<version>-linux-aarch64` |
| macOS (Intel) | `music-copyright-checker-<version>-macos-x86_64` |
| macOS (Apple Silicon / M-series) | `music-copyright-checker-<version>-macos-aarch64` |
| Windows x86_64 | `music-copyright-checker-<version>-windows-x86_64.exe` |

Each release also includes `SHA256SUMS` so you can verify the download:

```bash
# Linux / macOS
sha256sum -c SHA256SUMS --ignore-missing

# Windows (PowerShell)
Get-FileHash music-copyright-checker-*-windows-x86_64.exe -Algorithm SHA256
```

> macOS arm64 is built on Apple Silicon (Blacksmith), and macOS x86_64 on a
> GitHub-hosted Intel runner; Windows arm64 is not shipped. All binaries run
> natively on their target — no Rosetta or emulation required.

The binary is the same program whether you use it as a CLI or as a server.
It dispatches on the command:

```text
music-copyright-checker <CLI flags>            # one-shot check, prints JSON
music-copyright-checker server <server flags>  # long-running local JSON API
```

It also responds to being invoked through a file named `*server` (e.g. a
symlink or copy called `music-copyright-checker-server`), which matches the
pip package's `music-copyright-checker-server` entry point.

---

## 2. First run: it installs opencode for you

The binary does **not** bundle the `opencode` agent — that is a separate,
~60 MB, independently-updating distribution. On the first AI run, the binary
looks for `opencode` in this order:

1. an explicit path you passed with `--opencode-binary`;
2. the `PATH`;
3. the official install location `~/.opencode/bin`.

If none is found, it downloads it automatically:

| OS | Installer used |
| --- | --- |
| Linux / macOS | `curl -fsSL https://opencode.ai/install | bash -- --no-modify-path` |
| Windows | direct download of the official release zip, extracted to `%USERPROFILE%\.opencode\bin` |

The install prints its progress to stderr. `--no-modify-path` means your
shell rc files are never edited; the binary finds the CLI at
`~/.opencode/bin` directly. From the second run on, an existing install is
reused — no re-download.

You still need an authenticated LLM provider for the AI research step:

```bash
opencode auth login                # interactive provider setup
opencode auth list                 # verify credentials
opencode models                    # list models usable by this binary
```

Disable the auto-download with `--no-auto-install` on the CLI or server (it
then errors clearly if opencode is missing).

---

## 3. CLI usage

```bash
# Spotify track — URL, URI, or bare id
./music-copyright-checker --spotify-url https://open.spotify.com/track/6rqhFgbbKwnb9MLmUQDhG6 --pretty

# Local audio file (MP3/FLAC/M4A/OGG/WAV/...)
./music-copyright-checker --file ./song.mp3 --pretty

# Skip AI: only extract/normalize metadata
./music-copyright-checker --spotify-url spotify:track:xxxx --no-ai --pretty

# Force fresh research instead of using the cache
./music-copyright-checker --spotify-url spotify:track:xxxx --refresh

# Choose a different model
./music-copyright-checker --spotify-url spotify:track:xxxx --model opencode-go/deepseek-v4-pro

# Use a running `opencode serve` instead of spawning one process per check
./music-copyright-checker --spotify-url spotify:track:xxxx --opencode-server http://127.0.0.1:4096
```

Full flag list (`./music-copyright-checker --help`):

| Flag | Default | Meaning |
| --- | --- | --- |
| `--spotify-url` | — | Spotify track URL, URI, or bare id (mutually exclusive with `--file`) |
| `--file` | — | Path to a local audio file |
| `--model` | `opencode/big-pickle` | opencode model to run the research |
| `--opencode-server` | — | Talk to a running `opencode serve` base URL |
| `--opencode-binary` | `opencode` | opencode executable name/path |
| `--no-auto-install` | — | Do not download opencode if missing |
| `--timeout` | `900` | AI research timeout, seconds |
| `--no-ai` | — | Metadata only, skip the AI research step |
| `--cache-path` | `~/.cache/music-copyright-checker/cache.sqlite3` | SQLite cache location |
| `--no-cache` | — | Disable metadata and research caching |
| `--refresh` | — | Ignore cached values, force fresh lookups |
| `--pretty` | — | Pretty-print the JSON output |

Exit code is `0` on success. On a pipeline error the JSON
`{"error": "..."}` is written to stderr and the exit code is `1`.

The output is the full `CopyrightCheckResult` JSON — see
[Result shape](#7-result-shape) below.

---

## 4. Local JSON server (embed in another app)

The server is the integration surface for other applications. It is bound to
`127.0.0.1` by default and exposes a synchronous `/check` endpoint:

```bash
./music-copyright-checker server --host 127.0.0.1 --port 8080 --timeout 900
```

### Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/health` | Readiness + configured AI model |
| `GET` | `/docs` | Machine-readable API contract (endpoints, request shapes, examples) |
| `POST` | `/check` | Run the pipeline; synchronous JSON response |
| `GET/POST` | `/jobs*` | **Absent by default** — only enabled with `--jobs` |

The client binaries ship **without** the async `/jobs` queue. Local
integrations call `/check` and wait for the response, which is what the
`--timeout` flag bounds. Enable `--jobs` only for a server deployed behind a
proxy/Cloudflare where request timeouts force async processing.

### Server flags

| Flag | Default | Meaning |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Bind address |
| `--port` | `8080` | Bind port |
| `--model` | `opencode/big-pickle` | AI model |
| `--opencode-server` | — | Use a running `opencode serve` |
| `--opencode-binary` | `opencode` | opencode executable name/path |
| `--no-auto-install` | — | Do not download opencode if missing |
| `--jobs` | off | Enable the async `/jobs` queue (proxy deployments) |
| `--timeout` | `900` | Per-request AI research timeout |
| `--no-ai` | — | Metadata-only server (for API testing) |
| `--cache-path` / `--no-cache` | cache on | SQLite cache control |

---

## 5. Calling the server from your app

### Spotify URL (curl)

```bash
curl -X POST http://127.0.0.1:8080/check \
  -H 'Content-Type: application/json' \
  -d '{"spotify_url":"https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"}'
```

### Local file already on the server machine

```bash
curl -X POST http://127.0.0.1:8080/check \
  -H 'Content-Type: application/json' \
  -d '{"file":"/absolute/path/on/server/song.mp3"}'
```

### Upload a file from the client

```bash
curl -X POST http://127.0.0.1:8080/check -F 'file=@/path/to/song.mp3'
```

The server stores the upload temporarily, extracts tags, runs research, and
deletes the file. Uploads are limited to 100 MB.

### Public audio URL

```bash
curl -X POST http://127.0.0.1:8080/check \
  -H 'Content-Type: application/json' \
  -d '{"file_url":"https://cdn.example.com/song.mp3"}'
```

Only public HTTP/HTTPS URLs that resolve to a public internet address are
accepted, with a 100 MB download limit.

### Python

```python
import json
import urllib.request

def check(url):
    body = json.dumps({"spotify_url": url}).encode()
    request = urllib.request.Request(
        "http://127.0.0.1:8080/check",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=900) as response:
        return json.load(response)

print(check("https://open.spotify.com/track/6rqhFgbbKwnb9MLmUQDhG6"))
```

### JavaScript / TypeScript

```javascript
const result = await fetch("http://127.0.0.1:8080/check", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ spotify_url }),
}).then((r) => r.json());
```

A `"refresh": true` field in the JSON body forces fresh research, bypassing
cached results. CORS headers are set so browser clients can call a
localhost-bound server.

---

## 6. Caching

- Cache lives in SQLite at
  `~/.cache/music-copyright-checker/cache.sqlite3` by default.
- Spotify metadata: cached by canonical track ID for 12 hours.
- Local-file metadata: keyed by a streaming SHA-256 of the audio, 30 days.
  Audio bytes are never stored.
- AI research: cached 7 days, keyed by track + prompt version + model.
- Send `{"refresh":true}` (or `--refresh` on the CLI) to bypass the cache.

`ai_meta` reports `cache_hit`, `cache_age_seconds`, and `cached_at`.

---

## 7. Result shape

```json
{
  "request": {
    "source": "spotify",
    "input": "https://open.spotify.com/track/...",
    "track": { "name": "...", "artists": ["..."], "album": "...", "isrc": "..." },
    "credits": { "available": true, "performers": [...], "songwriters": [...], "producers": [...] }
  },
  "research": {
    "status": "complete | partial | not_found",
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
    "sources": [ { "name": "...", "url": "...", "source_type": "...", "supports": "..." } ],
    "usage_assessment": {
      "video_verdict": "clearance_required",
      "social_media_verdict": "clearance_required",
      "reality_tv_verdict": "clearance_required",
      "sync_license_required": true,
      "master_license_required": true,
      "platform_exception": "...",
      "caveats": ["..."]
    },
    "official_licensing_contacts": ["https://..."],
    "warnings": ["..."]
  },
  "ai_meta": { "mode": "process", "model": "...", "cost": 0.01, "tokens": { "total": 44157, "input": ..., "output": ..., "cache": {...} }, "session": "...", "cache_hit": false, "metadata_cache_hit": false }
}
```

The AI is instructed to only report what it actually found and to flag
disagreements in `warnings`. Treat `matches[].confidence` as a signal, not a
guarantee — this is research assistance, not a legal opinion.

---

## 8. Building the binaries yourself

Requires Python 3.9+.

```bash
uv sync --extra build --extra dev        # or: pip install ".[build,dev]"
uv run python -m PyInstaller --noconfirm --distpath dist packaging/music_copyright_checker.spec
uv run python packaging/smoke_test.py dist/music-copyright-checker
```

The spec (`packaging/music_copyright_checker.spec`) produces one self-
contained executable per platform. PyInstaller does **not** cross-compile —
build on each target OS/arch. The smoke test starts the binary, exercises the
CLI, boots the server, and asserts the `/jobs` endpoints are absent.

---

## 9. How releases are produced

`.github/workflows/build-binaries.yml` builds on push to `main`/`master`,
on `workflow_dispatch`, and on `v*` tags:

1. **Matrix build**:
   - `linux-x86_64` → `blacksmith-4vcpu-ubuntu-2404`
   - `linux-aarch64` → `blacksmith-4vcpu-ubuntu-2404-arm`
   - `windows-x86_64` → `blacksmith-4vcpu-windows-2025`
   - `macos-aarch64` → `blacksmith-6vcpu-macos-latest` (Apple Silicon)
   - `macos-x86_64` → `macos-26-intel` (GitHub-hosted; Blacksmith has no Intel macOS)
2. Each job installs the project, runs the full test suite, builds with
   PyInstaller, and smoke-tests the artifact.
3. Artifacts are uploaded to the workflow run.
4. On a `vX.Y.Z` tag, a `release` job downloads all binaries, writes
   `SHA256SUMS`, and publishes them as GitHub Release assets (with generated
   release notes).

The version in the asset names comes from `pyproject.toml`, so tagging
`v0.2.0` publishes `music-copyright-checker-0.2.0-linux-x86_64` and so on.

> **Blacksmith note.** Blacksmith runners are available to GitHub
> *organization* repositories with the Blacksmith app installed. On a
> personal repository, swap each `blacksmith-*` label for its GitHub-hosted
> equivalent — `ubuntu-24.04`, `ubuntu-24.04-arm`, `windows-2025`, `macos-26`
> — to run the same build for free. macOS Intel has no Blacksmith image, so
> that job uses the GitHub-hosted `macos-26-intel` runner regardless.

---

## 10. Troubleshooting

**"opencode binary not found" and it won't auto-install**
You passed `--no-auto-install`, or the install step failed. Install manually:
`curl -fsSL https://opencode.ai/install | bash`, then retry. On Windows use a
package manager (`scoop install opencode`, `choco install opencode`) or grab
the release zip from the opencode GitHub releases.

**AI run fails with an auth error**
The opencode CLI is installed but has no authenticated provider. Run
`opencode auth login` once and pick a provider, then retry.

**Server binds but `POST /check` times out**
The AI research is synchronous and can take minutes. Either raise
`--timeout` (and your client timeout) or point the server at a running
`opencode serve` with `--opencode-server` to avoid per-call process startup.

**Checksum mismatch**
Binaries are rebuilt per release; never mix a `SHA256SUMS` from one release
with assets from another. Verify against the matching release's file.

**"URL must resolve to a public internet address"**
`file_url` must be a public HTTP/HTTPS URL; the server refuses localhost,
private, and link-local hosts for its own safety.