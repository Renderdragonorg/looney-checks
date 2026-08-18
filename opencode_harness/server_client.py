"""Client for the ``opencode serve`` REST API.

Verified against opencode 1.18.13:

* ``POST /session`` with ``{"directory": ...}`` creates a session.
* ``POST /session/:id/message`` with ``{"parts": [{"type": "text", "text": ...}]}``
  blocks until the run finishes and returns ``{"info": ..., "parts": [...]}``.
* ``POST /session/:id/abort`` aborts a running prompt.

Basic auth is used when ``OPENCODE_SERVER_PASSWORD`` (or the ``password``
argument) is set, matching ``opencode serve``'s behaviour.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, Iterator, List, Optional

from .errors import OpenCodeError, HarnessTimeout
from .events import Event, Result, result_from_message


class ServerClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:4096",
        *,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: float = 600.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._auth: Optional[str] = None
        if password is None:
            password = os.environ.get("OPENCODE_SERVER_PASSWORD")
        if password:
            user = username or os.environ.get("OPENCODE_SERVER_USERNAME", "opencode")
            token = base64.b64encode(f"{user}:{password}".encode()).decode()
            self._auth = f"Basic {token}"

    # ------------------------------------------------------------------ http

    def _request(self, method: str, path: str, body: Optional[dict] = None,
                 *, timeout: Optional[float] = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"content-type": "application/json"}
        if self._auth:
            headers["authorization"] = self._auth
        req = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                payload = resp.read()
                if not payload:
                    return None
                return json.loads(payload.decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:1000]
            raise OpenCodeError(
                f"opencode server {method} {path} -> {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise OpenCodeError(
                f"cannot reach opencode server at {self.base_url}: {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise OpenCodeError(
                f"timed out talking to opencode server at {self.base_url}"
            ) from exc

    # ------------------------------------------------------------------ api

    def create_session(
        self,
        directory: Optional[str] = None,
        *,
        id: Optional[str] = None,
        model: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a session on the server. Returns the session object."""
        body: Dict[str, Any] = {}
        if directory is not None:
            body["directory"] = directory
        if id is not None:
            body["id"] = id
        if model is not None:
            body["model"] = model
        if title is not None:
            body["title"] = title
        session = self._request("POST", "/session", body)
        if not isinstance(session, dict) or "id" not in session:
            raise OpenCodeError(f"unexpected create_session response: {session!r}")
        return session

    def send_message(
        self,
        session_id: str,
        prompt: str,
        *,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Send a prompt. Blocks until the run finishes. Returns
        ``{"info": ..., "parts": [...]}``."""
        body: Dict[str, Any] = {"parts": [{"type": "text", "text": prompt}]}
        if model is not None:
            body["model"] = model
        resp = self._request(
            "POST", f"/session/{session_id}/message", body, timeout=timeout
        )
        if not isinstance(resp, dict):
            raise OpenCodeError(f"unexpected send_message response: {resp!r}")
        return resp

    def stream(
        self,
        session_id: str,
        prompt: str,
        *,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Iterator[Event]:
        """Send a prompt and yield events (synthesized from the response parts).

        The serve API has no SSE stream in 1.18.x, so the response is
        delivered atomically and events are yielded as they arrive in it.
        """
        resp = self.send_message(session_id, prompt, model=model, timeout=timeout)
        info = resp.get("info") or {}
        for part in resp.get("parts") or []:
            yield Event(
                type=part.get("type", "unknown"),
                part=part,
                session=info.get("sessionID"),
            )

    def call(
        self,
        session_id: str,
        prompt: str,
        *,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Result:
        """Send a prompt and return the assembled Result."""
        resp = self.send_message(session_id, prompt, model=model, timeout=timeout)
        return result_from_message(resp.get("info") or {}, resp.get("parts") or [])

    def list_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """List all messages (with parts) in a session."""
        resp = self._request("GET", f"/session/{session_id}/message")
        if not isinstance(resp, list):
            raise OpenCodeError(f"unexpected list_messages response: {resp!r}")
        return resp

    def abort(self, session_id: str) -> bool:
        """Abort the currently running prompt in a session."""
        return bool(self._request("POST", f"/session/{session_id}/abort"))

    def config(self, directory: Optional[str] = None, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Fetch the server's config (used for readiness checks)."""
        path = "/config"
        if directory:
            path += "?directory=" + urllib.parse.quote(directory)
        resp = self._request("GET", path, timeout=timeout)
        if not isinstance(resp, dict):
            raise OpenCodeError(f"unexpected config response: {resp!r}")
        return resp

    def wait_until_ready(self, timeout: float = 30.0) -> None:
        """Poll /config until the server answers or timeout elapses."""
        import time

        deadline = time.monotonic() + timeout
        last: Optional[str] = None
        while time.monotonic() < deadline:
            try:
                self.config(timeout=5.0)
                return
            except OpenCodeError as exc:
                last = str(exc)
                time.sleep(0.25)
        detail = f" ({last})" if last else ""
        raise HarnessTimeout(
            f"opencode server at {self.base_url} not ready after {timeout}s{detail}"
        )
