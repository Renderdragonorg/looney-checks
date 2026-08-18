# API Reference

## `OpenCode`

Unified entry point. Constructs one of the two backends and forwards calls to it. Defined in `opencode_harness/client.py`.

### Constructor

```python
OpenCode(
    server: str | None = None,
    *,
    username: str | None = None,
    password: str | None = None,
    binary: str = "opencode",
    auto_approve: bool = False,
    env: dict[str, str] | None = None,
)
```

- `server` — base URL of a running `opencode serve` (e.g. `"http://127.0.0.1:4096"`). When set, **server mode** is used and `binary`/`auto_approve`/`env` are ignored.
- `username` / `password` — Basic auth credentials for the server. `password` falls back to the `OPENCODE_SERVER_PASSWORD` env var; `username` falls back to `OPENCODE_SERVER_USERNAME` (default `"opencode"`).
- `binary` — path/name of the opencode executable (subprocess mode only).
- `auto_approve` — default for `--auto` (allow tool calls without confirmation) in subprocess mode.
- `env` — extra environment variables merged over the parent environment (subprocess mode only).

**Raises:** `OpenCodeError` if `server` mode isn't requested and the `binary` is not found on `PATH`.

### `mode` property

`str` — `"process"` or `"server"`, whichever backend was selected.

### `stream(prompt, *, session=None, directory=None, timeout=None, **kwargs) -> Iterator[Event]`

Run a prompt and yield events as they arrive.

- Subprocess mode: yields live events from stdout; kwargs are the `SubprocessClient` flags (see [subprocess-mode.md](subprocess-mode.md)).
- Server mode: if `session` is omitted, a session is created (using `directory` if given), then events are synthesized from the response parts. Kwargs: `model`, `timeout`.

### `call(prompt, *, session=None, directory=None, timeout=None, **kwargs) -> Result`

Run a prompt and return the fully assembled `Result`. Delegates to the backend's own `call` rather than the streaming path, so server mode retains the response-**info** metadata (`model`, `finish`, `cost`) that `build_result` over streamed parts would drop. Server mode auto-creates a session when `session` is omitted.

### `create_session(directory=None, *, id=None, model=None, title=None) -> dict`

**Server mode only.** Create a session on the server and return the session object (must contain `"id"`). Raises `OpenCodeError` in subprocess mode. See [server-mode.md](server-mode.md) for the full signature.

### `abort(session_id: str) -> bool`

**Server mode only.** Abort the currently running prompt in a session. Returns the (truthy) server response.

### `list_messages(session_id: str) -> list[dict]`

**Server mode only.** Return all messages (with parts) in a session.

---

## `ServerProcess`

Launches and manages an `opencode serve` process. Defined in `opencode_harness/client.py`.

```python
ServerProcess(
    *,
    port: int = 0,
    hostname: str = "127.0.0.1",
    binary: str = "opencode",
    env: dict[str, str] | None = None,
    extra: Iterable[str] = (),
    ready_timeout: float = 60.0,
    pidfile: str | None = None,
)
```

- `port` — listen port; `0` means "pick a free port" (bound, probed, released).
- `hostname` — bind address.
- `binary` — opencode executable.
- `env` — extra env vars for the server process.
- `extra` — additional CLI args appended to `opencode serve --port … --hostname …`.
- `ready_timeout` — max seconds to wait for `/config` to answer after launch.
- `pidfile` — optional path to persist `{pid, port, hostname, binary}` as JSON.

**Raises:** `OpenCodeError` at construction if the binary isn't found; `OpenCodeError` at `start()` if the server exits early (with the last 10 stderr lines attached to the message).

### Properties

- `base_url` → `f"http://{hostname}:{port}"`
- `pid` → current process PID (`int | None`)

### Methods

- `alive() -> bool` — whether the recorded PID is still running (`os.kill(pid, 0)` semantics; `PermissionError` counts as alive).
- `start() -> ServerProcess` — spawn `opencode serve`, write the pidfile, poll `/config` until ready. Idempotent: no-op if already started/attached.
- `stop() -> None` — terminate the server. Verifies the PID still belongs to an `opencode serve` process (via `/proc/<pid>/cmdline`) before signaling, raises `OpenCodeError` if the PID was reused, SIGTERMs and waits up to 10s, then SIGKILLs. Removes the pidfile. `ProcessLookupError` (already gone) is swallowed.
- `client(**kwargs) -> OpenCode` — a server-mode `OpenCode` pointing at `self.base_url`.
- `__enter__` / `__exit__` — context manager: `start()` on entry, `stop()` on exit.

### Class method: `attach(pidfile: str) -> ServerProcess`

Load a handle from a pidfile written by `start()`. Validates, in order:

1. The pidfile exists and parses as JSON — else `OpenCodeError`.
2. `pid` is present and `> 0` — else `OpenCodeError`.
3. The PID is alive — else `OpenCodeError` (stale pidfile; remove it to start fresh).
4. `/proc/<pid>/cmdline` contains `serve` and the recorded binary — else `OpenCodeError` (PID reused).

If `/proc` is unavailable the cmdline check is skipped (falls back to the liveness check only). The returned handle's `stop()` will only terminate that exact, verified process.

---

## `SubprocessClient`

Backend that runs `opencode run --format json` as a child process. Usually constructed via `OpenCode`, but usable directly. Defined in `opencode_harness/subprocess_client.py`.

```python
SubprocessClient(binary: str = "opencode", *, env=None, auto_approve: bool = False)
```

- `binary` — executable (checked on `PATH`; missing → `OpenCodeError`).
- `env` — merged over `os.environ`.
- `auto_approve` — default value for the `--auto` flag.

### `stream(prompt, *, directory=None, timeout=None, capture_stderr=False, **kwargs) -> Iterator[Event]`

Runs the prompt and yields `Event` objects parsed from each stdout JSONL line. Raises `HarnessTimeout` if `timeout` elapses (kills the process); raises `OpenCodeError` with the exit code (and, if `capture_stderr=True`, the last 20 stderr lines) on non-zero exit.

Kwargs forwarded to the CLI (see [subprocess-mode.md](subprocess-mode.md) for the flag mapping): `model`, `agent`, `session`, `fork`, `files`, `auto_approve`, `title`, `thinking`, `variant`, `pure`, `attach`, `extra`.

### `call(prompt, *, timeout=None, **kwargs) -> Result`

Convenience: `build_result(self.stream(...))`.

---

## `ServerClient`

Backend for the `opencode serve` REST API. Usually constructed via `OpenCode`. Defined in `opencode_harness/server_client.py`.

```python
ServerClient(base_url="http://127.0.0.1:4096", *, username=None, password=None, timeout=600.0)
```

- `base_url` — server root (trailing slash stripped).
- `username` / `password` — Basic auth; `password` falls back to `OPENCODE_SERVER_PASSWORD`, `username` to `OPENCODE_SERVER_USERNAME` (default `"opencode"`).
- `timeout` — default per-request timeout in seconds.

### Methods

- `create_session(directory=None, *, id=None, model=None, title=None) -> dict` — `POST /session`; returns the session object; raises `OpenCodeError` if no `id` comes back.
- `send_message(session_id, prompt, *, model=None, timeout=None) -> dict` — `POST /session/:id/message`; blocks until the run finishes; returns `{"info": …, "parts": […]}`.
- `stream(session_id, prompt, *, model=None, timeout=None) -> Iterator[Event]` — like `send_message`, but yields one `Event` per response part (synthesized; the serve API has no SSE stream in 1.18.x).
- `call(session_id, prompt, *, model=None, timeout=None) -> Result` — assembled result from the response.
- `list_messages(session_id) -> list[dict]` — `GET /session/:id/message`.
- `abort(session_id) -> bool` — `POST /session/:id/abort`.
- `config(directory=None, *, timeout=None) -> dict` — `GET /config`, optionally with a `directory` query param (used for readiness).
- `wait_until_ready(timeout=30.0) -> None` — poll `config()` every 0.25s until it succeeds; raises `HarnessTimeout` with the last error attached.

### HTTP errors

All HTTP/network failures surface as `OpenCodeError` with a descriptive message (`"opencode server METHOD /path -> status: detail"`, connection errors, or timeouts).

---

## `Event` — see [events-and-results.md](events-and-results.md)

## `Result` — see [events-and-results.md](events-and-results.md)

## Exceptions — see [errors.md](errors.md)