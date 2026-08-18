# Subprocess Mode

Subprocess mode drives the opencode CLI directly: every prompt becomes one `opencode run --format json` invocation, and events are streamed live from its stdout. This is the default and needs no server.

## How it works

`SubprocessClient.stream()` (`opencode_harness/subprocess_client.py:84`):

1. Builds the command line from kwargs (see flag mapping below).
2. Spawns the process with `stdout=PIPE`, `stderr=DEVNULL` (or `PIPE` when `capture_stderr=True`), text mode, utf-8 with `errors="replace"`, and `cwd=directory`.
3. Iterates stdout line by line; each line is parsed by `parse_event()` (malformed/empty lines are skipped) and yielded.
4. After the stream is exhausted, waits on the process:
   - If a `timeout` was set and it fired (a daemon `threading.Timer` kills the process), raises `HarnessTimeout(f"opencode run timed out after {timeout}s")`.
   - If the exit code is non-zero, raises `OpenCodeError` with the exit code, plus (when `capture_stderr=True`) the last 20 stderr lines as diagnostics.

The timer is cancelled in a `finally` block on normal completion, so a prompt that finishes just under the wire is not killed.

## CLI flag mapping

`_command()` (`subprocess_client.py:35`) maps keyword arguments to `opencode run` flags. Everything below can be passed to `OpenCode.stream()` / `OpenCode.call()` (server mode ignores them):

| Kwarg | Flag | Notes |
| --- | --- | --- |
| `directory` | `--dir <path>` | working directory (also used as `cwd` for the process) |
| `model` | `--model <name>` | model to use |
| `agent` | `--agent <name>` | agent (e.g. `general`, `build`) |
| `session` | `--session <id>` | continue an existing session |
| `fork` | `--fork` | fork a new session from the given one |
| `files` | `--file <path>` (repeatable) | attach files to the prompt |
| `auto_approve` | `--auto` | auto-approve tool calls; defaults to the client-level `auto_approve` unless explicitly set (explicit `False` suppresses the flag even if the client default is on) |
| `title` | `--title <text>` | session title |
| `thinking` | `--thinking` | enable thinking |
| `variant` | `--variant <name>` | variant override |
| `pure` | `--pure` | pure mode (no tools) |
| `attach` | `--attach <id>` | attach to a running session |
| `extra` | *(raw)* | extra CLI args appended verbatim before the prompt |
| `capture_stderr` | *(not a flag)* | capture stderr for diagnostics instead of discarding it |

The prompt is always the final argument. The `--format json` flag is always prepended.

## Example

```python
from opencode_harness import OpenCode

oc = OpenCode(auto_approve=True)

result = oc.call(
    "Refactor the database helpers",
    directory="/path/to/repo",
    files=["src/db.py", "src/models.py"],
    model="sonnet",
    timeout=300.0,
)
print(result.text)
print(result.model, result.tokens, result.cost)
```

## Behavior notes

- **Cold start:** every call spawns a fresh process; allow for model/binary startup time when budgeting timeouts.
- **Live streaming:** `stream()` yields events as they arrive — use it for progress UIs or log tails. `call()` is just `stream()` collected and aggregated.
- **Env:** `env` passed to the client is merged over `os.environ`; pass e.g. API keys or `OPENCODE_SERVER_PASSWORD`-style variables here.
- **Timeout semantics:** the timeout covers the whole prompt execution (spawn → last event). On fire, the child is `kill()`ed; there is no graceful shutdown first.
- **Diagnostics:** when a run fails, set `capture_stderr=True` to include the last 20 stderr lines in the raised `OpenCodeError` — helpful for auth/model-config problems.
