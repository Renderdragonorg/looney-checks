"""Tiny stdlib-only ``.env`` loader (no python-dotenv dependency).

The package reads configuration such as ``OPENROUTER_API_KEY`` from the
environment. To make local development convenient, this module loads a ``.env``
file (if present) into ``os.environ`` without ever overriding variables that are
already set in the real environment.

Looked up in order (first existing file wins per variable):

1. ``$MUSIC_CHECKER_ENV_FILE`` if set
2. ``./.env`` and ``./.env.local``
3. the repo/package root ``.env`` (parent of this package)
4. ``~/.config/music-copyright-checker/.env`` and ``~/.config/music-copyright-checker.env``

The loader is intentionally forgiving: malformed lines are skipped rather than
raising, so a stray entry can never stop the app from starting.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional


def parse_env_file(text: str) -> Dict[str, str]:
    """Parse ``KEY=VALUE`` lines, ignoring blanks/comments and stripping quotes."""
    values: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        name, separator, value = line.partition("=")
        if not separator:
            continue
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if name:
            values[name] = value
    return values


def candidate_paths() -> List[Path]:
    """Return the ordered list of ``.env`` locations to try."""
    paths: List[Path] = []
    override = os.environ.get("MUSIC_CHECKER_ENV_FILE")
    if override:
        paths.append(Path(override).expanduser())

    cwd = Path.cwd()
    paths.append(cwd / ".env")
    paths.append(cwd / ".env.local")
    paths.append(Path(__file__).resolve().parent.parent / ".env")

    home = Path.home()
    paths.append(home / ".config" / "music-copyright-checker" / ".env")
    paths.append(home / ".config" / "music-copyright-checker.env")

    seen = set()
    unique: List[Path] = []
    for path in paths:
        resolved = str(path)
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def load_env_file(
    *,
    paths: Optional[Iterable[Path]] = None,
    override: bool = False,
) -> List[str]:
    """Load env vars from the first matching files; return the names loaded.

    Existing environment variables are preserved unless ``override=True``, so
    real process env always wins over the ``.env`` file.
    """
    loaded: List[str] = []
    for path in (list(paths) if paths is not None else candidate_paths()):
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for name, value in parse_env_file(text).items():
            if override or name not in os.environ:
                os.environ[name] = value
                loaded.append(name)
    return loaded
