"""Client that drives ``opencode run --format json`` as a subprocess.

Streams live events from stdout. Prefer this mode when you want real-time
events without managing a server; each call has a cold start.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from typing import Any, Dict, Iterable, Iterator, List, Optional

from .errors import OpenCodeError, HarnessTimeout
from .events import Event, Result, build_result, parse_event


class SubprocessClient:
    def __init__(
        self,
        binary: str = "opencode",
        *,
        env: Optional[Dict[str, str]] = None,
        auto_approve: bool = False,
    ):
        if shutil.which(binary) is None:
            raise OpenCodeError(f"opencode binary not found: {binary!r}")
        self.binary = binary
        self.env = dict(os.environ)
        if env:
            self.env.update(env)
        self.auto_approve = auto_approve

    def _command(
        self,
        prompt: str,
        *,
        directory: Optional[str] = None,
        model: Optional[str] = None,
        agent: Optional[str] = None,
        session: Optional[str] = None,
        fork: bool = False,
        files: Iterable[str] = (),
        auto_approve: Optional[bool] = None,
        title: Optional[str] = None,
        thinking: bool = False,
        variant: Optional[str] = None,
        pure: bool = False,
        attach: Optional[str] = None,
        extra: Iterable[str] = (),
    ) -> List[str]:
        cmd = [self.binary, "run", "--format", "json"]
        if directory is not None:
            cmd += ["--dir", directory]
        if model:
            cmd += ["--model", model]
        if agent:
            cmd += ["--agent", agent]
        if session:
            cmd += ["--session", session]
        if fork:
            cmd.append("--fork")
        for f in files:
            cmd += ["--file", f]
        if auto_approve is None:
            auto_approve = self.auto_approve
        if auto_approve:
            cmd.append("--auto")
        if title:
            cmd += ["--title", title]
        if thinking:
            cmd.append("--thinking")
        if variant:
            cmd += ["--variant", variant]
        if pure:
            cmd.append("--pure")
        if attach:
            cmd += ["--attach", attach]
        cmd.extend(extra)
        cmd.append(prompt)
        return cmd

    def stream(
        self,
        prompt: str,
        *,
        directory: Optional[str] = None,
        timeout: Optional[float] = None,
        capture_stderr: bool = False,
        **kwargs: Any,
    ) -> Iterator[Event]:
        """Run one prompt and yield events as they arrive."""
        cmd = self._command(prompt, **kwargs)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if capture_stderr else subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=directory,
            env=self.env,
        )

        timer: Optional[threading.Timer] = None
        killed = False
        if timeout is not None:
            def _kill() -> None:
                nonlocal killed
                killed = True
                proc.kill()

            timer = threading.Timer(timeout, _kill)
            timer.daemon = True
            timer.start()

        stderr_tail: List[str] = []
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                ev = parse_event(line)
                if ev is not None:
                    yield ev
        finally:
            if timer is not None:
                timer.cancel()
            proc.stdout.close()  # type: ignore[union-attr]

        rc = proc.wait()
        if killed:
            raise HarnessTimeout(f"opencode run timed out after {timeout}s")
        if rc != 0:
            if capture_stderr:
                stderr_tail = (proc.stderr.read().splitlines() or [])[-20:]  # type: ignore[union-attr]
            detail = "\n".join(stderr_tail)
            raise OpenCodeError(f"opencode run exited with code {rc}\n{detail}".rstrip())

    def call(
        self,
        prompt: str,
        *,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> Result:
        """Run one prompt and return the assembled Result."""
        return build_result(self.stream(prompt, timeout=timeout, **kwargs))
