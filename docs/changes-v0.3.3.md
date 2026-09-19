# v0.3.3 — Exa Web Search, OpenAI-compatible Backend, AI Fallback

Feature release. Adds provider-agnostic web search, a generic OpenAI-compatible
backend, and a primary/secondary failover chain.

- Release: <https://github.com/Renderdragonorg/looney-checks/releases/tag/v0.3.3>
- Previous version: `0.3.2`

---

## 1. Web search for any provider (Exa)

Until now live research only worked on OpenRouter and OpenCode Go, because both
implement OpenRouter's server-side `openrouter:web_search` tool. Other
OpenAI-compatible providers had no way to search.

v0.3.3 adds [`exa_search.py`](../music_copyright_checker/exa_search.py): a
client-side `web_search` function tool backed by [Exa](https://docs.exa.ai/).
When a provider can't use the server tool, the client drives a bounded tool loop
(up to 6 rounds) — the model calls `web_search`, the pipeline queries Exa,
appends the results as tool output, and calls the model again.

Select it with `--search-backend {auto,server,exa,none}` (default `auto`):

| Mode | Behaviour |
| --- | --- |
| `auto` | Server tool when supported, Exa otherwise |
| `server` | Force `openrouter:web_search` |
| `exa` | Force the client-side Exa tool (needs `EXA_API_KEY`) |
| `none` | Disable web search |

A missing `EXA_API_KEY` raises `ExaSearchError` before any completion is billed.

---

## 2. Generic OpenAI-compatible backend

New backend `openai-compatible` (`OpenAICompatibleClient` /
`OpenAICompatibleResearcher`) targets any endpoint that speaks the OpenAI
`/chat/completions` shape but not OpenRouter's server tool:

```bash
export OPENAI_COMPAT_API_KEY=...
export OPENAI_COMPAT_MODEL=gpt-4.1-mini
music-copyright-checker --youtube-url "..." --ai-backend openai-compatible --model gpt-4.1-mini
```

- Base URL defaults to `https://api.openai.com/v1` (`OPENAI_COMPAT_BASE_URL`).
- A model is required (`--model`, `openai_compatible_model=`, or
  `OPENAI_COMPAT_MODEL`).
- Web search uses Exa (see §1), so `EXA_API_KEY` is required when enabled.

---

## 3. AI fallback chain (primary + secondaries)

Any backend list can be chained; the pipeline tries the primary first and falls
back in order when an endpoint fails (missing key, 401/403, rate limit,
timeout, bad response).

```python
pipeline = Pipeline(
    ai_backend="openrouter",
    ai_fallback_backends=["opencode-go", "openai-compatible"],
    openai_compatible_model="gpt-4.1-mini",
    ai_models={"opencode-go": "mimo-v2.5-pro"},
)
```

```bash
music-copyright-checker --youtube-url "..." \
  --ai-backend openrouter \
  --fallback-ai-backend opencode-go \
  --fallback-ai-backend openai-compatible \
  --openai-compatible-model gpt-4.1-mini
```

`ai_meta` now reports `provider`, `fallback_used`, `fallback_attempts`, and
`fallback_failures`. If everything fails, `AllBackendsFailedError` (a subclass
of `AIResearchError`) is raised. The research cache key covers the whole chain,
so a fallback result is never served as the primary's.

---

## 4. Also in this release

- **OpenCode Go backend** (`--ai-backend opencode-go`, model `mimo-v2.5`), a
  direct REST backend for `opencode.ai/zen/go` with the required Cloudflare
  `User-Agent`/`x-opencode-session` headers and reasoning disabled by default.

---

## 5. New configuration

| Env var | Purpose |
| --- | --- |
| `EXA_API_KEY` | Exa web search for non-server-tool providers |
| `OPENAI_COMPAT_API_KEY` | Generic OpenAI-compatible backend key |
| `OPENAI_COMPAT_BASE_URL` | Generic endpoint base URL |
| `OPENAI_COMPAT_MODEL` | Generic endpoint model |

New errors: `ExaSearchError`, `OpenAICompatibleError`, `AllBackendsFailedError`
(all subclass `AIResearchError`).

New exports: `ExaSearchClient`, `OpenAICompatibleClient`,
`OpenAICompatibleResearcher`, `FallbackResearcher`.

See the updated [AI backends guide](ai-backends.md) for details.

---

## 6. Upgrade notes

- Existing OpenRouter / OpenCode Go / opencode usage is unchanged; `auto` keeps
  using the server tool where available.
- Version bumped `0.3.2 → 0.3.3` (`pyproject.toml`, `__version__`).
- Cache keys are unchanged for single-backend setups.
