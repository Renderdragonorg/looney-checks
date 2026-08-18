# Server Mode

Server mode talks to a long-lived `opencode serve` daemon over its REST API. It gives you persistent sessions (with directory/model/title), abort of running prompts, and message history. Verified against opencode **1.18.13**.

## Two ways in

1. **Point at an existing server** (run `opencode serve --port 4096` yourself, or in Docker):

   ```python
   from opencode_harness import OpenCode

   oc = OpenCode(server="http://127.0.0.1:4096")
   sess = oc.create_session("/path/to/project")
   r = oc.call("summarize this repo", session=sess["id"])
   ```

2. **Let the harness launch one** via `ServerProcess`:

   ```python
   from opencode_harness import ServerProcess

   with ServerProcess(port=4096, pidfile="/tmp/oc.pid") as server:
       oc = server.client()
       sess = oc.create_session()
       print(oc.call("hi", session=sess["id"]).text)
   ```

## REST endpoints used

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/session` | `POST` | create a session; body `{directory?, id?, model?, title?}` → session object |
| `/session/:id/message` | `POST` | send a prompt; body `{parts: [{type: "text", text}] , model?}`; **blocks** until the run finishes → `{info, parts}` |
| `/session/:id/message` | `GET` | list all messages (with parts) in the session |
| `/session/:id/abort` | `POST` | abort the running prompt |
| `/config` | `GET` | server config; optional `?directory=`; used for readiness checks |

Notes:

- The serve API in 1.18.x has **no SSE stream** — `POST .../message` returns the whole run atomically. `ServerClient.stream()` therefore synthesizes one `Event` per response part and yields them in order; the interface matches subprocess mode, but delivery is not live.
- Metadata (`modelID`, `finish`, `cost`, `tokens`) lives in the response **`info`** block, not in the parts. The streaming path cannot see it, so prefer `call()` (which delegates to the backend's `call` → `result_from_message`) over `build_result(stream(...))` when you need `model`/`finish`.
- `send_message` / `stream` / `call` accept an optional `model` override sent in the request body.

## Sessions

- `create_session(directory=None, *, id=None, model=None, title=None)` — `directory` is the working directory for the session; `id` lets you request a specific session id.
- `OpenCode.create_session` raises `OpenCodeError` in subprocess mode; server mode is required for sessions.
- A session is needed before any message call. `OpenCode.stream()`/`call()` auto-create one when `session=` is omitted.

## Authentication

Basic auth is used when a password is in play, matching `opencode serve`'s behaviour:

- `OpenCode(server=..., username=..., password=...)` / `ServerClient(..., username=..., password=...)`, or
- environment: `OPENCODE_SERVER_PASSWORD` (and optionally `OPENCODE_SERVER_USERNAME`, default `"opencode"`).

Credentials are base64-encoded into an `Authorization: Basic …` header on every request. Prefer the env-var route over hard-coding passwords in source.

## Readiness & errors

- `ServerClient.wait_until_ready(timeout=30.0)` polls `GET /config` every 0.25 s until it answers; raises `HarnessTimeout` (last error included) otherwise. Used by `ServerProcess.start()`.
- HTTP status errors, connection failures, and timeouts all raise `OpenCodeError` with a message shaped like `opencode server POST /session/x/message -> 500: <detail>` (detail truncated to 1000 chars).

## `ServerProcess` lifecycle

### Launch (`start()`)

1. Validate the binary exists (already done at construction).
2. Resolve the port: `0` → bind a temporary socket to `hostname:0`, read the free port, close.
3. Spawn `opencode serve --port <port> --hostname <hostname> [extra…]` with `stdout=DEVNULL`, stderr to a temp log file (kept for diagnostics), and `env` (parent env + overrides).
4. Write the pidfile (atomic: temp file + `os.replace`).
5. Poll `wait_until_ready(ready_timeout)`.
6. On failure: if the process exited early, raise `OpenCodeError` including the exit code, a hint to run the serve command manually, and the **last 10 stderr lines** from the log.

### Tear down (`stop()`)

1. Verify the PID is still an `opencode serve` process via `/proc/<pid>/cmdline` (contains `serve` and the binary name) — otherwise raise `OpenCodeError` ("pid reused?"). Note: only the recorded PID is ever touched, never process groups.
2. If we spawned it: `proc.terminate()`, wait up to 10 s, then `kill()` if needed.
3. If attached: `SIGTERM`, poll for exit up to 10 s, then `SIGKILL`.
4. Remove the pidfile. `ProcessLookupError` is swallowed (already gone).

### Pidfile attach (`ServerProcess.attach(pidfile)`)

Useful after an app restart or from another process, when a `stop()` was never called (crash, etc.):

```python
srv = ServerProcess.attach("/tmp/oc.pid")
print(srv.pid, srv.alive())
srv.stop()
```

`attach()` validates, in order: pidfile exists & parses (`OpenCodeError` otherwise), pid > 0, PID alive, and `/proc/<pid>/cmdline` matches an `opencode serve` invocation. If `/proc` is unavailable the cmdline check is skipped. This makes killing a recycled PID impossible. A stale pidfile must be removed manually to start a fresh server.

### Context manager

`with ServerProcess(...) as server:` = `start()` on entry, `stop()` on exit (even on exceptions).

## Operational notes

- One server can host many sessions and concurrent message calls; sessions are independent.
- A prompt that blocks forever is bounded by `timeout` (constructor default 600 s, overridable per call).
- The serve process's stderr log is a temp file (`opencode-serve-*.log`); if a prompt fails mysteriously, run the serve command manually to see the full logs.
- `extra` args are appended after `--port`/`--hostname` — e.g. `extra=["--password", "secret"]` to secure the server (then pass the same password to `OpenCode(server=..., password=...)`).
