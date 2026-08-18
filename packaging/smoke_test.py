"""Cross-platform smoke test for a freshly built binary (run by CI).

Validates that the packaged executable starts, exposes the CLI, and serves
the local JSON API (jobs endpoint absent by default) without needing network
or an opencode install.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    binary = Path(sys.argv[1])
    if not binary.is_file():
        print(f"binary not found: {binary}", file=sys.stderr)
        return 1

    print("== CLI entry point ==")
    help_result = subprocess.run(
        [str(binary), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if help_result.returncode != 0 or "usage" not in help_result.stdout.lower():
        print(f"CLI --help failed:\n{help_result.stdout}\n{help_result.stderr}", file=sys.stderr)
        return 1

    print("== local server (no jobs) ==")
    port = free_port()
    server = subprocess.Popen(
        [str(binary), "server", "--no-ai", "--no-cache", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        health = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if server.poll() is not None:
                out, err = server.communicate()
                print(f"server exited early ({server.returncode})\n{out}\n{err}", file=sys.stderr)
                return 1
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                    health = json.load(response)
                    break
            except Exception:
                time.sleep(0.25)
        if health is None or health.get("status") != "ok":
            print("server never became healthy", file=sys.stderr)
            return 1

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/docs", timeout=5) as response:
            docs = json.load(response)
        job_paths = [e["path"] for e in docs["endpoints"]]
        if any(p.startswith("/jobs") for p in job_paths):
            print("client binary unexpectedly exposes /jobs endpoints", file=sys.stderr)
            return 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()

    print("smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())