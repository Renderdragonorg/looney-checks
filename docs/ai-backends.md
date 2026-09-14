# AI Backends Guide

The licensing research step ("given this track's metadata, who owns it and what
licences are needed?") is pluggable. The pipeline supports two backends:

| Backend | Value | Transport | Default model | Needs |
| --- | --- | --- | --- | --- |
| **OpenRouter** (default) | `openrouter` | Direct HTTPS REST call to `openrouter.ai` | `openrouter/free` | `OPENROUTER_API_KEY` |
| **opencode** | `opencode` | Local `opencode` CLI agent via the vendored `opencode_harness` | `opencode-go/mimo-v2.5` | `opencode` binary + provider auth |

Both produce the same `ResearchResult`, so `Pipeline.check_*()` and the JSON
response contract are identical regardless of backend.

---

## 1. How the research step works

1. The pipeline normalizes a source (Spotify / YouTube / local file) into a
   `LookupRequest` and serializes it (minus any raw source payload).
2. `prompts.build_research_prompt()` wraps that payload in the research
   instructions (identify the track, visit authoritative sources, separate
   composition/sync rights from master rights, never invent URLs, emit strict
   JSON).
3. The backend runs the prompt:
   - **OpenRouter** — one `POST /api/v1/chat/completions` with the
     `openrouter:web_search` server tool enabled, so the model can search the
     live web itself.
   - **opencode** — spawns `opencode run --format json` and lets the agent use
     its own web-browsing tools.
4. `ai_researcher.parse_research_response()` parses the model's reply
   (tolerating code fences / stray prose) into `matches`, `sources`,
   `usage_assessment`, `official_licensing_contacts`, and `warnings`.

---

## 2. OpenRouter backend (default)

### 2.1 Get and configure a key

1. Create an API key at <https://openrouter.ai/keys>.
2. Export it, or put it in a `.env` file (see §5):

```bash
export OPENROUTER_API_KEY=sk-or-...
```

No key is ever hardcoded in the repo.

### 2.2 Model selection

The default model is `openrouter/free`, OpenRouter's free model router. It
picks a free, **tool-capable** model per request and reports which one actually
answered in `ai_meta.model`.

```bash
# Free router (default)
--model openrouter/free

# A specific model, e.g. a paid one with stronger reasoning
--model anthropic/claude-sonnet-4.5
```

Free variants exist with a `:free` suffix (e.g. `meta-llama/llama-3.2-3b-instruct:free`).
Free models can be slower or rate-limited; if research quality/latency matters,
pin a specific model.

### 2.3 Request shape

The client sends an OpenAI-compatible chat completion:

```json
{
  "model": "openrouter/free",
  "messages": [{ "role": "user", "content": "<research prompt>" }],
  "tools": [
    { "type": "openrouter:web_search", "parameters": { "max_results": 5 } }
  ],
  "usage": { "include": true }
}
```

`openrouter:web_search` is an OpenRouter **server tool**: the model decides
when/how often to search and OpenRouter executes it server-side — there is no
client-side tool loop to implement. Set `openrouter_web_search=False` to
disable it (not recommended for licensing research).

### 2.4 Reliability

- Reads `usage.cost` and token counts; surfaces them in `ai_meta`.
- Retries transient failures (`408, 409, 429, 500, 502, 503, 504` and
  TLS/network errors) up to 3 times with backoff.
- `401`/`403` become a clear `OpenRouterError` naming `OPENROUTER_API_KEY`.
- Uses `certifi`'s CA bundle, so it works on python.org macOS builds and inside
  frozen binaries where the system keychain is unavailable.

---

## 3. opencode backend (optional)

Use this to keep the previous behaviour (a local coding agent with its own
browser/search tools).

```bash
--ai-backend opencode --model opencode-go/mimo-v2.5
```

- Requires the `opencode` CLI on `PATH` (or `--opencode-server` for a running
  `opencode serve`).
- Downloads opencode on first run unless `--no-auto-install` is passed.
- Needs an authenticated provider (`opencode auth login`).
- Default model is `opencode-go/mimo-v2.5`; default timeout is 900s.
- Reference docs for the driver live in [opencode-harness/](opencode-harness/).

---

## 4. Configuring the backend

### Python

```python
from music_copyright_checker import Pipeline

# OpenRouter (default)
pipeline = Pipeline()                       # ai_backend="openrouter", model="openrouter/free"
pipeline = Pipeline(ai_model="anthropic/claude-sonnet-4.5")
pipeline = Pipeline(openrouter_api_key="sk-or-...")  # else OPENROUTER_API_KEY / .env

# opencode
pipeline = Pipeline(ai_backend="opencode", ai_model="opencode-go/mimo-v2.5")

# Metadata only (skip AI)
pipeline = Pipeline(run_ai_research=False)
```

Relevant `Pipeline` parameters:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `ai_backend` | `"openrouter"` | `"openrouter"` or `"opencode"` |
| `ai_model` | backend default | Model override for the active backend |
| `openrouter_api_key` | `None` | Falls back to `OPENROUTER_API_KEY` / `.env` |
| `openrouter_base_url` | `https://openrouter.ai/api/v1` | API base URL |
| `openrouter_web_search` | `True` | Use the `openrouter:web_search` server tool |
| `openrouter_timeout` | `300.0` | Per-request timeout (seconds) |

The legacy `opencode_*` parameters (`opencode_server`, `opencode_binary`,
`opencode_model`, `opencode_timeout`, `opencode_username`, `opencode_password`,
`opencode_auto_approve`, `opencode_auto_install`) still work when
`ai_backend="opencode"`.

### CLI / server

```bash
# CLI
music-copyright-checker --youtube-url "https://www.youtube.com/watch?v=..." --ai-backend openrouter --model openrouter/free
music-copyright-checker --spotify-url spotify:track:xxxx --ai-backend opencode --model opencode-go/mimo-v2.5

# Server
music-copyright-checker-server --host 127.0.0.1 --port 8080 --ai-backend openrouter --model openrouter/free --timeout 300
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--ai-backend` | `openrouter` | `openrouter` or `opencode` |
| `--model` | backend default | Model override |
| `--timeout` | `300` (OpenRouter) / `900` (opencode) | Research timeout, seconds |
| `--opencode-server` | — | `opencode serve` base URL (opencode backend) |
| `--opencode-binary` | `opencode` | opencode executable |
| `--no-auto-install` | — | Don't download opencode if missing |

---

## 5. Configuration via `.env`

A stdlib-only loader (`music_copyright_checker/env.py`) runs on package import
and loads the first matching file into `os.environ`. Real environment variables
always win; `.env` values never override them.

Looked up in order:

1. `$MUSIC_CHECKER_ENV_FILE`
2. `./.env`, `./.env.local`
3. the repository root `.env`
4. `~/.config/music-copyright-checker/.env`, `~/.config/music-copyright-checker.env`

Example `.env` (git-ignored — never commit it):

```dotenv
OPENROUTER_API_KEY=sk-or-...
YOUTUBE_API_KEY=AIza...
```

---

## 6. Response metadata

Every result carries `ai_meta`. For OpenRouter it looks like:

```json
{
  "mode": "openrouter",
  "model": "inclusionai/ling-3.0-flash-fin:free",
  "session": "gen-...",
  "cost": 0.0,
  "tokens": { "input": 3216, "output": 1613, "total": 33481, "reasoning": 604 },
  "cache_hit": false,
  "metadata_cache_hit": false
}
```

`mode` distinguishes the backend. `model` is the model that actually served the
request (for the free router this differs from the requested `openrouter/free`).

---

## 7. Caching

Research is cached in SQLite (`~/.cache/music-copyright-checker/cache.sqlite3`)
by `research:v1:<identity>:<prompt_version>:<model>:<payload_hash>`. The model
string and `RESEARCH_PROMPT_VERSION` are part of the key, so switching backend
or model naturally misses the old cache. `refresh=true` (JSON) or `--refresh`
(CLI) bypasses the cache.

---

## 8. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `OpenRouter API key is not configured` | Set `OPENROUTER_API_KEY` or pass `openrouter_api_key=`. Fails in ~1s. |
| `OpenRouter rejected the API key (401/403)` | Bad/expired key, or no credit on the account. Check <https://openrouter.ai/keys>. |
| `OpenRouter returned an empty completion` | The routed free model returned nothing; retry or pin a specific model. |
| Research takes very long | Free models can be queued/throttled. Pin a paid/faster model or lower `--timeout`. |
| `ai_model` is `null` in `/health` | Server started with `--no-ai`. |

> The old opencode subprocess backend could occasionally stall for a long time
> while producing no streamed output. That failure mode does **not** apply to
> the OpenRouter backend, which is a single HTTP request bounded by
> `openrouter_timeout`.

---

## 9. Security

- Keep `OPENROUTER_API_KEY` (and `YOUTUBE_API_KEY`) server-side only; never send
  them from a browser client.
- The repo ships no keys; `.env` is git-ignored.
- Rotate any key that has been pasted into a chat, screenshot, or commit.
