# Events & Results

The data model shared by both backends lives in `opencode_harness/events.py`. One prompt produces a **stream of `Event`s**; the stream can be consumed directly or **aggregated into a `Result`**.

## `Event`

```python
@dataclass
class Event:
    type: str                       # event type, e.g. "message.part.updated", "message.updated"
    part: dict                      # the payload (what opencode calls a part)
    timestamp: int = 0              # epoch ms from the JSONL line
    session: str | None = None      # sessionID
    raw: dict = {}                  # the complete original JSON line (subprocess mode)
```

### Convenience properties

| Property | Returns |
| --- | --- |
| `event.is_text` | `True` when `part["type"] == "text"` |
| `event.is_finish` | `True` when `part["type"]` is one of `"step-finish"`, `"agent"`, `"meta"` (message-level summary parts) |
| `event.text` | `part.get("text")` (the text chunk, if any) |
| `event.tool` | `part.get("tool")` (tool invocation data, if any) |

### Where events come from

- **Subprocess mode:** every stdout line of `opencode run --format json` is parsed by `parse_event()`; non-JSON and blank lines are skipped (`None`). `type`, `part`, `timestamp`, `sessionID` are read from the line; the whole line is kept in `raw`.
- **Server mode:** there is no SSE stream in opencode 1.18.x, so `ServerClient.stream()` turns each part of the atomic `POST /session/:id/message` response into `Event(type=part["type"], part=part, session=info["sessionID"])` and yields them in order.

Typical `part["type"]` values encountered in streams: `"text"` (assistant text chunks), `"reasoning"` (reasoning text), `"tool"` (tool calls), `"step-finish"` / `"agent"` / `"meta"` (run metadata: cost, tokens, model, finish reason).

## `Result`

`call()` and `build_result()` produce an aggregated summary.

```python
@dataclass
class Result:
    text: str = ""                      # all text chunks joined with "\n"
    reasoning: str = ""                 # last reasoning chunk
    session: str | None = None
    events: list[Event] = field(default_factory=list)  # full event stream
    cost: float = 0.0                   # USD from the finish part
    tokens: dict = {}                   # token usage breakdown
    model: str | None = None
    finish: str | None = None           # finish reason (server mode; e.g. "success", "error")
```

## Aggregation rules (`build_result`)

`build_result(events)` (`opencode_harness/events.py:74`) walks the event stream once:

- `session` — taken from the first event that carries one.
- `text` — every `is_text` event's `part["text"]` is collected; joined with `"\n"`.
- `reasoning` — the last `part["type"] == "reasoning"` chunk with text.
- `cost` / `tokens` / `model` — from the first finish-ish part (`step-finish`/`agent`/`meta`) that has them; `cost` is `float()`, non-numeric values are ignored.
- `events` — the full list is retained for callers that want the raw detail.

## `result_from_message(info, parts)`

Server-mode variant: builds events from a `POST /session/:id/message` response, runs the same aggregation, then **overrides** `model`, `session`, `cost`, `tokens`, and `finish` from `info` (the response `info` block is authoritative in server mode).

## Streaming vs. call

```python
# Streaming: act on each event as it arrives (progress UIs, logs)
for ev in oc.stream("refactor it"):
    if ev.is_text and ev.text:
        print("tokens:", ev.text, flush=True)

# Aggregation: single value
result = oc.call("refactor it")
print(result.text)          # final answer
print(result.cost)          # ~ dollar cost
print(result.tokens)        # usage dictionary
print(result.session)       # session id (for follow-up calls)
```

## Notes for implementers

- `Event.part` is always a plain dict — the schema is whatever opencode emits; the harness adds no fields except positional ones on the dataclass. Treat unknown `part["type"]` values as opaque. (Top-level event `type`s appear as `step_start` while the corresponding `part.type` is `step-start` — the harness keys off `part.type`.)
- `is_finish` is deliberately broad (`step-finish`, `agent`, `meta`) because the summary part's type varies by mode/version; pick data from the first one that carries it.
- `Result.model` is only set when a `modelID` field is present: subprocess `--format json` events in 1.18.x carry **no** `modelID` (it lives only in the serve API `info`), so subprocess-mode `Result.model` is `None` by design. Prefer server mode (`call()`) when you need the model ID.
- `Result.finish` is set only by `result_from_message` from the server `info.finish`. Subprocess parts expose the reason under `part.reason` (e.g. `"stop"`), which `build_result` does not currently map to `finish`.
- Text chunking is not guaranteed word-aligned; a single sentence may arrive as several `text` events. Join with `"\n"` (as `build_result` does) before rendering.
- In server mode the `stream()` iterator is not truly live (the HTTP call blocks until completion); prefer subprocess mode when event-level latency matters.