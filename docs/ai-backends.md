# AI Backends Guide

The licensing research step ("given this track's metadata, who owns it and what
licences are needed?") is pluggable. The pipeline supports several backends,
and any of them can be chained as a primary/secondary fallback:

| Backend | Value | Transport | Default model | Needs |
| --- | --- | --- | --- | --- |
| **OpenRouter** (default) | `openrouter` | Direct HTTPS REST call to `openrouter.ai` | `openrouter/free` | `OPENROUTER_API_KEY` |
| **OpenCode Go** | `opencode-go` | Direct HTTPS REST call to `opencode.ai/zen/go` | `mimo-v2.5` | `OPENCODE_GO_API_KEY` |
| **OpenAI-compatible** | `openai-compatible` | Direct HTTPS REST call to any OpenAI-compatible endpoint | none (must be set) | `OPENAI_COMPAT_API_KEY` + model |
| **opencode** | `opencode` | Local `opencode` CLI agent via the vendored `opencode_harness` | `opencode-go/mimo-v2.5` | `opencode` binary + provider auth |

All produce the same `ResearchResult`, so `Pipeline.check_*()` and the JSON
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
   - **OpenRouter / OpenCode Go** — one `POST /chat/completions` with the
     `openrouter:web_search` server tool enabled, so the model can search the
     live web itself.
   - **OpenAI-compatible** — `POST /chat/completions` with a client-side
     `web_search` function tool; when the model calls it, the pipeline queries
     Exa and feeds the results back (see §2d).
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

## 2b. OpenCode Go backend (`opencode-go`)

A second direct REST backend, aimed at the OpenCode Go ("zen/go") endpoint,
which speaks the same OpenRouter-compatible chat-completions API:

```bash
--ai-backend opencode-go --model mimo-v2.5
```

- Auth comes from `OPENCODE_GO_API_KEY` (or `opencode_go_api_key=...`).
- Base URL defaults to `https://opencode.ai/zen/go/v1`; default model is
  `mimo-v2.5`; default timeout is 300s.
- The endpoint sits behind Cloudflare, which rejects the stock Python
  user-agent (error 1010), so requests send a `User-Agent` **and** an
  `x-opencode-session` header automatically.
- Live web research works: the endpoint accepts the `openrouter:web_search`
  server tool and returns the same `url_citation` annotations as OpenRouter.
- `mimo-v2.5` is a reasoning model; the client sends
  `reasoning: {"enabled": false}` so reasoning tokens don't consume the output
  budget and leave the visible JSON empty/truncated (pass
  `disable_reasoning=False` to `OpenCodeGoClient` to keep reasoning on).

---

## 2c. OpenAI-compatible backend (`openai-compatible`)

For any hosted or self-hosted endpoint that speaks the OpenAI
`/chat/completions` shape but does not implement OpenRouter's server-side
search tool (OpenAI, Groq, Together, vLLM, a local gateway, ...).

```bash
export OPENAI_COMPAT_API_KEY=...
export OPENAI_COMPAT_BASE_URL=https://api.openai.com/v1   # optional, this is the default
export OPENAI_COMPAT_MODEL=gpt-4.1-mini                   # or pass --model / model=

music-copyright-checker --youtube-url "..." --ai-backend openai-compatible --model gpt-4.1-mini
```

- A model is **required** (via `--model`, `openai_compatible_model=`, or
  `OPENAI_COMPAT_MODEL`); construction fails fast without one.
- `openai_compatible_api_key_env` / `--openai-compatible-api-key-env` let you
  point at a different key variable.
- Because the endpoint has no `openrouter:web_search`, live research runs
  through the client-side Exa tool in §2d, so `EXA_API_KEY` is **required** when
  web search is enabled.

---

## 2d. Web search (`server` vs `exa`)

Web search has two transports, selected with `web_search_backend` /
`--search-backend`:

| Mode | Behaviour |
| --- | --- |
| `auto` (default) | Use `openrouter:web_search` when the backend supports it (OpenRouter, OpenCode Go); otherwise use the Exa function tool. |
| `server` | Force `openrouter:web_search`. Errors on providers that don't support it. |
| `exa` | Force the client-side Exa function tool. Requires `EXA_API_KEY`. |
| `none` | Disable web search entirely. |

The Exa path advertises a normal function tool to the model and runs a bounded
tool loop (up to 6 rounds): the model calls `web_search`, the pipeline queries
Exa's REST API, appends the results as a `role: "tool"` message, and calls the
model again until it produces a final answer.

```bash
export EXA_API_KEY=...
music-copyright-checker --youtube-url "..." --ai-backend openai-compatible --model gpt-4.1-mini --search-backend exa
```

**Exa is required for providers without the OpenRouter server tool** — a
missing `EXA_API_KEY` raises `ExaSearchError` before any completion is billed.

---

## 2e. AI fallback chain (primary + secondaries)

Any backend list can be chained: the pipeline tries the primary first and
falls back to each configured endpoint in order when one fails (missing key,
401/403, rate limit, timeout, bad response).

```python
# Python: OpenRouter primary, OpenCode Go then a generic endpoint as fallbacks
pipeline = Pipeline(
    ai_backend="openrouter",
    ai_fallback_backends=["opencode-go", "openai-compatible"],
    openai_compatible_model="gpt-4.1-mini",
    ai_models={"opencode-go": "mimo-v2.5-pro"},   # optional per-backend model
)
```

```bash
# CLI / server: repeat the flag for a longer chain
music-copyright-checker --youtube-url "..." \
  --ai-backend openrouter \
  --fallback-ai-backend opencode-go \
  --fallback-ai-backend openai-compatible \
  --openai-compatible-model gpt-4.1-mini
```

`ai_meta` records the outcome:

```json
{
  "provider": "OpenCode Go",
  "fallback_used": true,
  "fallback_attempts": 2,
  "fallback_failures": [{ "provider": "OpenRouter", "error": "..." }]
}
```

When every endpoint fails the pipeline raises `AllBackendsFailedError` (a
subclass of `AIResearchError`). The research cache key covers the whole chain,
so a fallback result is never served as if the primary produced it.

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

# OpenCode Go (OpenRouter-compatible endpoint; needs OPENCODE_GO_API_KEY)
pipeline = Pipeline(ai_backend="opencode-go")                 # model="mimo-v2.5"
pipeline = Pipeline(ai_backend="opencode-go", ai_model="mimo-v2.5-pro")

# opencode
pipeline = Pipeline(ai_backend="opencode", ai_model="opencode-go/mimo-v2.5")

# Metadata only (skip AI)
pipeline = Pipeline(run_ai_research=False)
```

Relevant `Pipeline` parameters:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `ai_backend` | `"openrouter"` | Primary backend: `"openrouter"`, `"opencode-go"`, `"openai-compatible"`, or `"opencode"` |
| `ai_fallback_backends` | `None` | Ordered secondary backends tried if the primary fails |
| `ai_model` | backend default | Model override for the primary backend |
| `ai_models` | `None` | Per-backend model overrides, e.g. `{"opencode-go": "mimo-v2.5-pro"}` |
| `web_search_backend` | `"auto"` | `"auto"`, `"server"`, `"exa"`, or `"none"` |
| `exa_api_key` | `None` | Falls back to `EXA_API_KEY` / `.env` |
| `exa_base_url` | `https://api.exa.ai` | Exa API base URL |
| `exa_timeout` | `30.0` | Exa search timeout (seconds) |
| `openrouter_api_key` | `None` | Falls back to `OPENROUTER_API_KEY` / `.env` |
| `openrouter_base_url` | `https://openrouter.ai/api/v1` | API base URL |
| `openrouter_web_search` | `True` | Allow web search on this backend |
| `openrouter_timeout` | `300.0` | Per-request timeout (seconds) |
| `opencode_go_api_key` | `None` | Falls back to `OPENCODE_GO_API_KEY` / `.env` |
| `opencode_go_base_url` | `https://opencode.ai/zen/go/v1` | OpenCode Go API base URL |
| `opencode_go_web_search` | `True` | Allow web search on this backend |
| `opencode_go_timeout` | `300.0` | Per-request timeout (seconds) |
| `openai_compatible_api_key` | `None` | Falls back to `OPENAI_COMPAT_API_KEY` / `.env` |
| `openai_compatible_base_url` | `None` | Falls back to `OPENAI_COMPAT_BASE_URL` (default `https://api.openai.com/v1`) |
| `openai_compatible_model` | `None` | Falls back to `OPENAI_COMPAT_MODEL` (required) |
| `openai_compatible_api_key_env` | `None` | Override the key env var name |
| `openai_compatible_web_search` | `True` | Allow web search (Exa) on this backend |
| `openai_compatible_timeout` | `300.0` | Per-request timeout (seconds) |

The legacy `opencode_*` parameters (`opencode_server`, `opencode_binary`,
`opencode_model`, `opencode_timeout`, `opencode_username`, `opencode_password`,
`opencode_auto_approve`, `opencode_auto_install`) still work when
`ai_backend="opencode"`.

### CLI / server

```bash
# CLI
music-copyright-checker --youtube-url "https://www.youtube.com/watch?v=..." --ai-backend openrouter --model openrouter/free
music-copyright-checker --spotify-url spotify:track:xxxx --ai-backend opencode-go --model mimo-v2.5
music-copyright-checker --spotify-url spotify:track:xxxx --ai-backend openai-compatible --model gpt-4.1-mini
music-copyright-checker --spotify-url spotify:track:xxxx --ai-backend opencode --model opencode-go/mimo-v2.5

# Server
music-copyright-checker-server --host 127.0.0.1 --port 8080 --ai-backend opencode-go --model mimo-v2.5 --timeout 300
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--ai-backend` | `openrouter` | Primary backend (`openrouter`, `opencode-go`, `openai-compatible`, `opencode`) |
| `--fallback-ai-backend` | — | Secondary backend; repeat for a longer chain |
| `--model` | backend default | Model override for the primary backend |
| `--search-backend` | `auto` | `auto`, `server`, `exa`, or `none` |
| `--openai-compatible-base-url` | `OPENAI_COMPAT_BASE_URL` | Base URL for the OpenAI-compatible backend |
| `--openai-compatible-model` | `OPENAI_COMPAT_MODEL` | Model for the OpenAI-compatible backend |
| `--openai-compatible-api-key-env` | `OPENAI_COMPAT_API_KEY` | Key env var for the OpenAI-compatible backend |
| `--timeout` | `300` (REST) / `900` (opencode) | Research timeout, seconds |
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
OPENCODE_GO_API_KEY=...
# Generic OpenAI-compatible backend (optional)
OPENAI_COMPAT_API_KEY=...
OPENAI_COMPAT_BASE_URL=https://api.openai.com/v1
OPENAI_COMPAT_MODEL=gpt-4.1-mini
# Web search for providers without OpenRouter's server tool (optional)
EXA_API_KEY=...
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
When a fallback chain is configured, `provider`, `fallback_used`,
`fallback_attempts`, and `fallback_failures` are added (see §2e).

---

## 7. Caching

Research is cached in SQLite (`~/.cache/music-copyright-checker/cache.sqlite3`)
by `research:v1:<identity>:<prompt_version>:<model>:<payload_hash>`. The model
string and `RESEARCH_PROMPT_VERSION` are part of the key, so switching backend
or model naturally misses the old cache. With a fallback chain, the key covers
every backend/model in the chain so a fallback result is not reused as the
primary's. `refresh=true` (JSON) or `--refresh` (CLI) bypasses the cache.

---

## 8. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `OpenRouter API key is not configured` | Set `OPENROUTER_API_KEY` or pass `openrouter_api_key=`. Fails in ~1s. |
| `OpenRouter rejected the API key (401/403)` | Bad/expired key, or no credit on the account. Check <https://openrouter.ai/keys>. |
| `OpenRouter returned an empty completion` | The routed free model returned nothing; retry or pin a specific model. |
| `OpenCode Go API key is not configured` | Set `OPENCODE_GO_API_KEY` or pass `opencode_go_api_key=`. |
| `OpenCode Go returned an empty completion` | Reasoning likely ate the output budget; the client disables reasoning by default, so check the model and `--timeout`. |
| `OpenCode Go request failed (1010)` | Cloudflare blocked the request signature; keep the auto-sent `User-Agent` / `x-opencode-session` headers. |
| `Exa web search is not configured` | A non-OpenRouter backend needs `EXA_API_KEY` (or `--search-backend none` to disable search). |
| `Exa rejected the API key (401/403)` | Bad/expired Exa key. Check <https://dashboard.exa.ai>. |
| `exceeded N web-search rounds` | The model kept calling `web_search` without answering; use a model with better tool use or raise `max_tool_rounds`. |
| `All AI backends failed (...)` | Every endpoint in the fallback chain failed; the message lists each provider's error. |
| Research takes very long | Free models can be queued/throttled. Pin a paid/faster model or lower `--timeout`. |
| `ai_model` is `null` in `/health` | Server started with `--no-ai`. |

> The old opencode subprocess backend could occasionally stall for a long time
> while producing no streamed output. That failure mode does **not** apply to
> the OpenRouter backend, which is a single HTTP request bounded by
> `openrouter_timeout`.

---

## 9. Security

- Keep `OPENROUTER_API_KEY`, `OPENCODE_GO_API_KEY`, `OPENAI_COMPAT_API_KEY`,
  `EXA_API_KEY` (and `YOUTUBE_API_KEY`) server-side only; never send them from a
  browser client.
- The repo ships no keys; `.env` is git-ignored.
- Rotate any key that has been pasted into a chat, screenshot, or commit.
