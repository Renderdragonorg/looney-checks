# Overview

`opencode-harness` is a thin, dependency-free Python layer over the opencode CLI and its HTTP serve API. Its job: let an application send prompts to opencode, receive events/results in a typed, predictable shape, and manage the surrounding process lifecycle — without re-implementing JSON handling, subprocess plumbing, or HTTP clients.

## Package layout

```
opencode_harness/
├── __init__.py          # public exports
├── client.py            # OpenCode (unified API) + ServerProcess (lifecycle)
├── subprocess_client.py # SubprocessClient: `opencode run --format json`
├── server_client.py     # ServerClient: REST API of `opencode serve`
├── events.py            # Event, Result + parsing/aggregation helpers
└── errors.py            # OpenCodeError, HarnessTimeout
```

Public API (re-exported from `__init__.py`):

- `OpenCode` — the unified front door
- `ServerProcess` — launch / stop / attach an `opencode serve` process
- `ServerClient`, `SubprocessClient` — the two backends (usually used via `OpenCode`)
- `Event`, `Result` — typed data models
- `OpenCodeError`, `HarnessTimeout` — errors

## The two backends

`OpenCode` picks a backend at construction time (`opencode_harness/client.py:39`):

| | Subprocess mode (default) | Server mode |
| --- | --- | --- |
| Constructor | `OpenCode()` | `OpenCode(server="http://127.0.0.1:4096")` |
| Backend | `SubprocessClient` | `ServerClient` |
| Transport | one `opencode run --format json` per prompt | HTTP against a persistent `opencode serve` |
| Streaming | live, event-by-event from stdout | response delivered atomically; events synthesized from response parts |
| Sessions | optional `--session <id>` flag | first-class: create / abort / list |
| Cold start | every call (new process) | none (daemon already running) |
| Use when | quick one-shots, no infra, want true streaming | repeated calls, persistent sessions, parallel workloads |

The mode is exposed as `OpenCode.mode` (`"process"` or `"server"`).

## Choosing a mode

- **Subprocess mode**: simplest. Nothing to manage; each call pays a cold-start cost of spawning a process. Events stream live. Good for batched jobs, CLIs, scripts.
- **Server mode**: a long-lived `opencode serve` handles all calls; sessions persist conversation state and can be aborted. Use `ServerProcess` in Python to launch it (or run `opencode serve` yourself and point at it).

Regardless of mode, `stream()`, `call()`, `Event`, `Result`, and the error classes behave consistently, so application code rarely needs to know which backend is in use.

## Dependency story

- Runtime: **stdlib only** (`subprocess`, `urllib`, `threading`, `json`, …). No third-party packages.
- External: the `opencode` binary on `PATH` (checked at construction time — a missing binary raises `OpenCodeError` immediately).
- Compatible with Python ≥ 3.9. Verified against opencode **1.18.13**.