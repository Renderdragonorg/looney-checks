# Errors

Two exception classes, defined in `opencode_harness/errors.py`:

```
OpenCodeError(Exception)
└── HarnessTimeout(OpenCodeError)
```

## `OpenCodeError`

Raised when opencode itself reports a failure. Subclass of `Exception`; catch it to handle any harness failure except genuine `TimeoutError`-family bugs.

Sources, by backend:

| Where | Conditions | Message shape |
| --- | --- | --- |
| Any construction | binary not found on `PATH` | `opencode binary not found: 'name'` |
| Subprocess run | non-zero exit code | `opencode run exited with code <rc>` + last 20 stderr lines if `capture_stderr=True` |
| Server HTTP | status >= 400 | `opencode server <METHOD> <path> -> <code>: <detail>` (detail truncated to 1000 chars) |
| Server HTTP | connection refused / DNS / etc. | `cannot reach opencode server at <url>: <reason>` |
| Server HTTP | socket timeout | `timed out talking to opencode server at <url>` |
| `create_session` | response without `id` | `unexpected create_session response: …` |
| `ServerProcess` | serve exited during startup | exit code + "try running `opencode serve --port …` manually" + last 10 stderr lines |
| `ServerProcess.stop` / `attach` | PID no longer an `opencode serve` process (reuse guard) | `refusing to terminate pid <n>: it is no longer an opencode serve process (pid reused?)` (or the stale-pidfile variant) |
| `attach` | unreadable/corrupt pidfile, dead PID | `cannot read pidfile …`, `pidfile … is corrupt: …`, `server recorded in … (pid …) is no longer running; remove the stale pidfile …` |

## `HarnessTimeout`

Subclass of `OpenCodeError` — raised when an operation exceeds its configured timeout. Catching `OpenCodeError` covers timeouts too; catch `HarnessTimeout` specifically when you want to treat timeouts differently from other failures.

| Where | Trigger | Message |
| --- | --- | --- |
| Subprocess mode | `timeout=` fires (daemon timer kills the process) | `opencode run timed out after <n>s` |
| Server readiness | `/config` never answers within the window | `opencode server at <url> not ready after <n>s` (+ last poll error) |

## Recommended handling

```python
from opencode_harness import OpenCode, HarnessTimeout, OpenCodeError

oc = OpenCode(auto_approve=True)

try:
    r = oc.call("fix the bug", timeout=120.0)
except HarnessTimeout:
    ...  # retry with a bigger budget, or degrade gracefully
except OpenCodeError as e:
    ...  # opencode-level failure: bad prompt, missing binary, server down
```

Notes:

- The subprocess timeout is enforced by `proc.kill()` from a daemon `threading.Timer`; the kill is not graceful.
- Server-side request timeouts surface as `OpenCodeError` (not `HarnessTimeout`), since they come from the HTTP layer; only `wait_until_ready` raises `HarnessTimeout` on the server side.
- `ServerProcess.stop()` swallowing `ProcessLookupError` is intentional: a server that already exited is not an error when stopping.