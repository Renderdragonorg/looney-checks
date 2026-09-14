# v0.3.0 — Changes and Migration

This release adds a YouTube source and replaces the default AI backend. It is a
**breaking change** for anyone relying on the previous default behaviour.

- Release: <https://github.com/Renderdragonorg/looney-checks/releases/tag/v0.3.0>
- Previous version: `0.2.4`

---

## 1. Summary of changes

### Added — YouTube Data API v3 source

- `music_copyright_checker/youtube_source.py`
  - `parse_video_id()` for watch/youtu.be/shorts/embed/live/v and
    `music.youtube.com` URLs, plus bare 11-character ids.
  - `videos.list` lookup and `search.list` free-text query resolution.
  - `normalize_video_info()` and best-effort credits (uploader +
    `Provided to YouTube by <label>`).
- `Pipeline.check_youtube_url(url_or_query)` with metadata caching by video id
  and query → video-id caching.
- New `TrackMetadata` fields: `youtube_id`, `youtube_url`, `channel`,
  `channel_id`, `channel_url`, `description`, `tags`, `category`,
  `licensed_content`, `published_at`, `view_count`, `thumbnail_url`.
- Server accepts `youtube_url`; CLI gains `--youtube-url`.
- Errors: `InvalidYouTubeURLError`, `YouTubeLookupError`, `YouTubeAPIError`.
- Tests: `tests/test_youtube.py`.
- Docs: [YouTube source guide](youtube-source.md).

### Added — OpenRouter AI backend (new default)

- `music_copyright_checker/openrouter_client.py` — direct OpenRouter REST client
  (`POST /api/v1/chat/completions`) with the `openrouter:web_search` server tool,
  certifi TLS, retries, and normalized result metadata.
- `music_copyright_checker/openrouter_researcher.py` — same
  `research(request) -> (ResearchResult, meta)` contract as the opencode backend.
- `Pipeline(ai_backend="openrouter")` (default), `ai_model`,
  `openrouter_api_key`, `openrouter_base_url`, `openrouter_web_search`,
  `openrouter_timeout`.
- CLI/server flags: `--ai-backend {openrouter,opencode}`, generic `--model`.
- Error: `OpenRouterError` (subclass of `AIResearchError`).
- Tests: `tests/test_openrouter.py`.
- Docs: [AI backends guide](ai-backends.md).

### Added — `.env` loading

- `music_copyright_checker/env.py`: stdlib-only loader (no `python-dotenv`).
  Loaded on package import; never overrides real environment variables.
- `.env` is git-ignored. Keys are no longer bundled in source.
- Tests: `tests/test_env.py`.

### Changed

- **Default AI backend is now OpenRouter** (`openrouter/free`) instead of the
  opencode CLI agent. The opencode backend remains available via
  `ai_backend="opencode"` / `--ai-backend opencode`.
- `DEFAULT_OPENCODE_MODEL` is `opencode-go/mimo-v2.5`;
  `DEFAULT_OPENROUTER_MODEL` is `openrouter/free`; default timeouts are 300s
  (OpenRouter) and 900s (opencode).
- Version bumped `0.2.4 → 0.3.0` (`pyproject.toml`, `__version__`).
- `Pipeline`'s internal AI-model cache-key attribute is `_ai_model` (was
  `_opencode_model`).
- The YouTube key is resolved lazily and is **no longer hardcoded**.

### Fixed

- The opencode subprocess backend could stall for a long time without
  producing output; the default OpenRouter backend is a single bounded HTTP
  request, so that failure mode is removed from the default path.

---

## 2. Migration from 0.2.x

### If you used the opencode harness

Set the environment variable and nothing else changes:

```bash
export OPENROUTER_API_KEY=sk-or-...
```

When a key is missing the pipeline fails in ~1 second with a clear message
instead of hanging.

### If you want to keep the opencode agent

```bash
--ai-backend opencode --model opencode-go/mimo-v2.5
```

```python
Pipeline(ai_backend="opencode", ai_model="opencode-go/mimo-v2.5")
```

### If you called `Pipeline(opencode_model=...)`

`opencode_model=` still works for the opencode backend. For the default
OpenRouter backend use `ai_model=`. Both can be expressed generically:

```python
Pipeline(ai_backend="openrouter", ai_model="anthropic/claude-sonnet-4.5")
```

### If you referenced `youtube_source.DEFAULT_YOUTUBE_API_KEY`

That constant was removed. Configure `YOUTUBE_API_KEY` (env or `.env`), pass
`youtube_api_key=`, or `YOUTUBE_API_KEY` will raise a clear error when a YouTube
request is made.

---

## 3. Suggested `.env`

```dotenv
OPENROUTER_API_KEY=sk-or-...
YOUTUBE_API_KEY=AIza...
```

Both are optional individually: Spotify/local-file checks work with only the
OpenRouter key; metadata-only runs (`--no-ai`) with a YouTube URL need only the
YouTube key.

---

## 4. Verification

- Test suite: `python -m pytest -q` (≥100 tests).
- Live YouTube metadata check: `--youtube-url <url> --no-ai`.
- Live full check (YouTube API + AI): `--youtube-url <url> --pretty`, responding
  with `request.source = "youtube"` and `ai_meta.mode = "openrouter"`.
- Release binaries for linux-x86_64/aarch64, macos-aarch64/x86_64 and
  windows-x86_64 are published with `SHA256SUMS`.
