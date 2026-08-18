"""Unified entry point: one API, two backends.

``OpenCode()`` defaults to spawning ``opencode run`` per prompt (streaming,
no server required). Pass ``server=`` to talk to a running ``opencode serve``
instance instead, or use ``ServerProcess`` to launch one from Python.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from typing import Any, Dict, Iterator, List, Optional

from .errors import OpenCodeError, HarnessTimeout
from .events import Event, Result, build_result
from .server_client import ServerClient
from .subprocess_client import SubprocessClient


class OpenCode:
    """Front door for driving opencode from your application.

    Example:
        >>> oc = OpenCode()                       # one-shot subprocess mode
        >>> oc.call("explain this error")

        >>> oc = OpenCode(server="http://127.0.0.1:4096")   # server mode
        >>> sess = oc.create_session("/path/to/project")
        >>> oc.call("fix the tests", session=sess["id"])
    """

    def __init__(
        self,
        server: Optional[str] = None,
        *,
        username: Optional[str] = None,
        password: Optional[str] = None,
        binary: str = "opencode",
        auto_approve: bool = False,
        env: Optional[Dict[str, str]] = None,
    ):
        if server is not None:
            self._mode = "server"
            self._client = ServerClient(server, username=username, password=password)
        else:
            self._mode = "process"
            self._client = SubprocessClient(
                binary, env=env, auto_approve=auto_approve
            )

    @property
    def mode(self) -> str:
        return self._mode

    # ------------------------------------------------------------------ api

    def stream(
        self,
        prompt: str,
        *,
        session: Optional[str] = None,
        directory: Optional[str] = None,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> Iterator[Event]:
        """Run a prompt, yielding events as they arrive.

        In subprocess mode events stream live from stdout; in server mode a
        session is created (if none given) and the response parts are
        yielded after the run completes.
        """
        if self._mode == "server":
            if session is None:
                session = self.create_session(directory).get("id")
                if session is None:
                    raise OpenCodeError("server returned a session without an id")
            yield from self._client.stream(session, prompt, timeout=timeout, **kwargs)
        else:
            yield from self._client.stream(
                prompt,
                directory=directory,
                session=session,
                timeout=timeout,
                **kwargs,
            )

    def call(
        self,
        prompt: str,
        *,
        session: Optional[str] = None,
        directory: Optional[str] = None,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> Result:
        """Run a prompt and return the assembled Result.

        Delegates to the backend's ``call`` (not the streaming path) so that
        server mode keeps the metadata in the response ``info`` block — model,
        finish, cost — which ``build_result`` over streamed parts drops.
        """
        if self._mode == "server":
            if session is None:
                session = self.create_session(directory).get("id")
                if session is None:
                    raise OpenCodeError(
                        "server returned a session without an id"
                    )
            return self._client.call(session, prompt, timeout=timeout, **kwargs)
        return self._client.call(
            prompt, directory=directory, session=session, timeout=timeout, **kwargs
        )

    def create_session(self, directory: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        if self._mode == "server":
            return self._client.create_session(directory, **kwargs)
        raise OpenCodeError("create_session is only available in server mode")

    def abort(self, session_id: str) -> bool:
        if self._mode == "server":
            return self._client.abort(session_id)
        raise OpenCodeError("abort is only available in server mode")

    def list_messages(self, session_id: str) -> List[Dict[str, Any]]:
        if self._mode == "server":
            return self._client.list_messages(session_id)
        raise OpenCodeError("list_messages is only available in server mode")


class ServerProcess:
    """Launches and manages an ``opencode serve`` process.

    Example:
        >>> with ServerProcess(port=4096) as server:
        ...     oc = server.client()
        ...     result = oc.call("hello", directory="/tmp")

    Pass ``pidfile=`` to persist the process PID. Later (e.g. after an app
    restart, or from a different process) call ``ServerProcess.attach(path)``
    to get a handle that validates the PID against ``/proc/<pid>/cmdline``
    before terminating it, so an unrelated process that reused the PID is
    never killed.
    """

    def __init__(
        self,
        *,
        port: int = 0,
        hostname: str = "127.0.0.1",
        binary: str = "opencode",
        env: Optional[Dict[str, str]] = None,
        extra: Iterable[str] = (),
        ready_timeout: float = 60.0,
        pidfile: Optional[str] = None,
    ):
        if shutil.which(binary) is None:
            raise OpenCodeError(f"opencode binary not found: {binary!r}")
        if port == 0:
            with socket.socket() as s:
                s.bind((hostname, 0))
                port = int(s.getsockname()[1])
        self.port = port
        self.hostname = hostname
        self.binary = binary
        self.env = dict(os.environ)
        if env:
            self.env.update(env)
        self.extra = list(extra)
        self.ready_timeout = ready_timeout
        self.pidfile = pidfile
        self._proc: Optional[subprocess.Popen] = None
        self._pid: Optional[int] = None
        self._attached = False
        self._stderr_name: Optional[str] = None

    @property
    def base_url(self) -> str:
        return f"http://{self.hostname}:{self.port}"

    @property
    def pid(self) -> Optional[int]:
        if self._proc is not None:
            return self._proc.pid
        return self._pid

    def alive(self) -> bool:
        """True if the recorded process is still running."""
        pid = self.pid
        return pid is not None and self._pid_alive(pid)

    # ----------------------------------------------------------- lifecycle

    def start(self) -> "ServerProcess":
        if self._proc is not None:
            return self
        if self._attached:
            return self
        cmd = [self.binary, "serve", "--port", str(self.port), "--hostname", self.hostname]
        cmd.extend(self.extra)
        tmp = tempfile.NamedTemporaryFile(
            mode="w", prefix="opencode-serve-", suffix=".log", delete=False
        )
        self._stderr_name = tmp.name
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=tmp, env=self.env
        )
        tmp.close()
        self._write_pidfile()
        try:
            ServerClient(self.base_url).wait_until_ready(self.ready_timeout)
        except Exception:
            if self._proc.poll() is not None:
                early = ""
                try:
                    with open(self._stderr_name) as fh:
                        tail = fh.read().splitlines()[-10:]
                    if tail:
                        early = "\n  stderr tail:\n" + "\n".join("    " + l for l in tail)
                except OSError:
                    pass
                raise OpenCodeError(
                    f"opencode serve exited early (code {self._proc.returncode}); "
                    f"try running `{self.binary} serve --port {self.port}` manually"
                    f"{early}"
                ) from None
            raise
        return self

    def stop(self) -> None:
        """Terminate the server. Only the recorded PID is touched, and only
        after verifying it still belongs to an ``opencode serve`` process."""
        pid = self.pid
        if pid is None:
            return
        if self._proc is not None and self._proc.poll() is not None:
            pid = None  # child already exited
        if pid is not None and not self._cmdline_matches(pid):
            raise OpenCodeError(
                f"refusing to terminate pid {pid}: it is no longer an "
                f"opencode serve process (pid reused?)"
            )
        try:
            if self._proc is not None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait()
            elif pid is not None:
                os.kill(pid, signal.SIGTERM)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if not self._pid_alive(pid):
                        break
                    time.sleep(0.1)
                else:
                    os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # already gone
        finally:
            self._proc = None
            self._pid = None
            self._remove_pidfile()

    def client(self, **kwargs: Any) -> OpenCode:
        return OpenCode(self.base_url, **kwargs)

    def __enter__(self) -> "ServerProcess":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # ------------------------------------------------------------- pidfile

    def _write_pidfile(self) -> None:
        if not self.pidfile or self.pid is None:
            return
        data = {
            "pid": self.pid,
            "port": self.port,
            "hostname": self.hostname,
            "binary": self.binary,
        }
        tmp = f"{self.pidfile}.tmp.{self.pid}"
        with open(tmp, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, self.pidfile)

    def _remove_pidfile(self) -> None:
        if self.pidfile:
            try:
                os.unlink(self.pidfile)
            except OSError:
                pass

    @classmethod
    def attach(cls, pidfile: str) -> "ServerProcess":
        """Load a handle from a pidfile written by ``start()``.

        Validates that the recorded process is alive and that its
        ``/proc/<pid>/cmdline`` matches an ``opencode serve`` invocation
        before handing back a handle whose ``stop()`` will terminate it.
        """
        try:
            with open(pidfile) as fh:
                data = json.load(fh)
        except OSError as exc:
            raise OpenCodeError(f"cannot read pidfile {pidfile}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise OpenCodeError(f"pidfile {pidfile} is corrupt: {exc}") from exc

        pid = int(data.get("pid", 0))
        if pid <= 0:
            raise OpenCodeError(f"pidfile {pidfile} has no valid pid")
        if not cls._pid_alive(pid):
            raise OpenCodeError(
                f"server recorded in {pidfile} (pid {pid}) is no longer "
                f"running; remove the stale pidfile to start a fresh server"
            )
        if not cls._cmdline_matches_pidfile(pid, data):
            raise OpenCodeError(
                f"pid {pid} from {pidfile} is not an opencode serve process "
                f"(pid reused?); remove the stale pidfile before retrying"
            )

        self = cls(
            port=int(data.get("port", 0)),
            hostname=str(data.get("hostname", "127.0.0.1")),
            binary=str(data.get("binary", "opencode")),
            pidfile=pidfile,
        )
        self._pid = pid
        self._attached = True
        return self

    # -------------------------------------------------------------- checks

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    @staticmethod
    def _cmdline(pid: int) -> Optional[str]:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                return fh.read().decode(errors="replace").replace("\0", " ")
        except OSError:
            return None

    def _cmdline_matches(self, pid: int) -> bool:
        return self._cmdline_matches_pidfile(pid, self._pidfile_data())

    def _pidfile_data(self) -> Dict[str, Any]:
        return {
            "pid": self.pid,
            "port": self.port,
            "hostname": self.hostname,
            "binary": self.binary,
        }

    @classmethod
    def _cmdline_matches_pidfile(cls, pid: int, data: Dict[str, Any]) -> bool:
        cmdline = cls._cmdline(pid)
        if cmdline is None:
            return True  # /proc unavailable; fall back to pid-alive check only
        return "serve" in cmdline and data.get("binary", "opencode") in cmdline
