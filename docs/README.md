# Documentation

Guides for the music copyright checker backend.

## Core guides

| Guide | What it covers |
| --- | --- |
| [Server API implementation guide](server-api.md) | Running the JSON server and calling `/check`, `/youtube/search`, `/jobs`, `/docs`, `/health` |
| [Downloadable binaries](binaries.md) | Prebuilt binaries, first-run setup, CLI flags, embedding, CI/release |
| [AI backends](ai-backends.md) | OpenRouter, OpenCode Go, OpenAI-compatible, and opencode backends; Exa web search, AI fallback chains, keys, models, `.env`, troubleshooting |
| [YouTube Data API v3 source](youtube-source.md) | Getting/configuring a key, accepted inputs, normalized fields, quota, examples |

## Release notes

| Guide | What it covers |
| --- | --- |
| [v0.3.3 release notes](changes-v0.3.3.md) | Exa web search, OpenAI-compatible backend, AI fallback chains, OpenCode Go |
| [v0.3.1 release notes](changes-v0.3.1.md) | onedir archives for fast startup, asset list, upgrading from 0.3.0 |
| [v0.3.0 changes and migration](changes-v0.3.0.md) | YouTube source + OpenRouter backend, breaking changes, migration from 0.2.x |

## Reference

| Guide | What it covers |
| --- | --- |
| [opencode-harness](opencode-harness/overview.md) | The vendored driver for the optional `opencode` backend (API, events, errors, modes) |

---

## Quick start

```bash
# 1. Configure keys (or use a .env file)
export OPENROUTER_API_KEY=sk-or-...
export YOUTUBE_API_KEY=AIza...

# 2. Run a check
music-copyright-checker --youtube-url "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --pretty
music-copyright-checker --spotify-url spotify:track:xxxx --pretty
music-copyright-checker --file ./song.mp3 --pretty

# 3. Or run the JSON server
music-copyright-checker-server --host 127.0.0.1 --port 8080
```

Request flow:

```text
source (Spotify / YouTube / file)
  -> normalized TrackMetadata + TrackCredits
  -> AI licensing research (OpenRouter by default)
  -> structured JSON (request + research + ai_meta)
```
