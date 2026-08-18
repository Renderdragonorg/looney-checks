"""Dispatch entry point for the packaged binaries.

A single PyInstaller binary per platform serves both the CLI and the local
JSON server, so integrations only need to download one file:

* ``music-copyright-checker <CLI args>`` — the original CLI.
* ``music-copyright-checker server <server args>`` — the local JSON API
  server (without the async ``/jobs`` queue, which client integrations do
  not need).

It also responds to being invoked through a symlink/copy whose basename ends
in ``-server`` (e.g. ``music-copyright-checker-server``), matching the pip
entry point name.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

try:
    from music_copyright_checker import cli, server  # frozen script (top-level)
except ImportError:
    from . import cli, server  # invoked as a package module


def _invoked_as_server() -> bool:
    basename = os.path.basename(sys.argv[0]).lower()
    stem = os.path.splitext(basename)[0]
    return stem.endswith("-server") or stem.endswith("_server")


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if _invoked_as_server() or (args and args[0] == "server"):
        if args and args[0] == "server":
            args = args[1:]
        return server.main(args)
    return cli.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
