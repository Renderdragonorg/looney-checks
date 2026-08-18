"""Typed event/result model shared by the subprocess and server clients."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class Event:
    """One opencode event.

    For ``opencode run --format json`` every stdout line is an event with a
    ``part`` payload. For the serve REST API, parts are synthesized into
    events so both modes expose the same streaming interface.
    """

    type: str
    part: Dict[str, Any] = field(default_factory=dict)
    timestamp: int = 0
    session: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_text(self) -> bool:
        return self.part.get("type") == "text"

    @property
    def is_finish(self) -> bool:
        return self.part.get("type") in ("step-finish", "agent", "meta")

    @property
    def text(self) -> Optional[str]:
        return self.part.get("text")

    @property
    def tool(self) -> Optional[Dict[str, Any]]:
        return self.part.get("tool")


@dataclass
class Result:
    """Assembled outcome of one prompt."""

    text: str = ""
    reasoning: str = ""
    session: Optional[str] = None
    events: List[Event] = field(default_factory=list)
    cost: float = 0.0
    tokens: Dict[str, Any] = field(default_factory=dict)
    model: Optional[str] = None
    finish: Optional[str] = None


def parse_event(line: str) -> Optional[Event]:
    """Parse one JSONL line from ``opencode run --format json`` stdout."""
    line = line.strip()
    if not line:
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    return Event(
        type=str(data.get("type", "unknown")),
        part=data.get("part", {}) or {},
        timestamp=int(data.get("timestamp", 0)),
        session=data.get("sessionID"),
        raw=data,
    )


def build_result(events: Iterable[Event]) -> Result:
    """Aggregate a stream of events into a Result (subprocess mode)."""
    events = list(events)
    chunks: List[str] = []
    result = Result(session=events[0].session if events else None, events=events)
    for ev in events:
        if ev.is_text and ev.text:
            chunks.append(ev.text)
        if ev.is_finish:
            part = ev.part
            result.cost = float(part.get("cost") or result.cost)
            result.tokens = part.get("tokens") or result.tokens
            if "modelID" in part:
                result.model = part["modelID"]
        if ev.part.get("type") == "reasoning" and ev.part.get("text"):
            result.reasoning = ev.part["text"]
    result.text = "\n".join(chunks)
    return result


def result_from_message(info: Dict[str, Any], parts: List[Dict[str, Any]]) -> Result:
    """Build a Result from a ``POST /session/:id/message`` response (server mode)."""
    events = [
        Event(type=p.get("type", "unknown"), part=p, session=info.get("sessionID"))
        for p in parts
    ]
    result = build_result(events)
    result.model = info.get("modelID") or result.model
    result.session = info.get("sessionID")
    result.cost = float(info.get("cost") or 0.0)
    result.tokens = info.get("tokens") or {}
    result.finish = info.get("finish")
    return result
