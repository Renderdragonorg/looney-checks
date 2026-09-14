"""Minimal CLI for exercising the pipeline while there's no UI yet.

Examples:
    python -m music_copyright_checker.cli --spotify-url https://open.spotify.com/track/xxxx
    python -m music_copyright_checker.cli --youtube-url https://www.youtube.com/watch?v=xxxx
    python -m music_copyright_checker.cli --youtube-url "Artist - Song name"
    python -m music_copyright_checker.cli --file ./song.mp3
    python -m music_copyright_checker.cli --spotify-url spotify:track:xxxx --no-ai --pretty
"""

from __future__ import annotations

import argparse
import json
import sys

from .errors import MusicCheckerError
from .ai_researcher import DEFAULT_OPENCODE_MODEL, DEFAULT_OPENCODE_TIMEOUT
from .openrouter_client import DEFAULT_OPENROUTER_MODEL, DEFAULT_OPENROUTER_TIMEOUT
from .pipeline import Pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Music copyright / licensing checker")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--spotify-url", help="A Spotify track URL, URI, or bare track id.")
    source.add_argument(
        "--youtube-url",
        help="A YouTube video URL, bare 11-character video id, or a free-text search query.",
    )
    source.add_argument("--file", help="Path to a local audio file.")

    parser.add_argument(
        "--ai-backend",
        choices=("openrouter", "opencode"),
        default="openrouter",
        help="AI backend: OpenRouter REST API (default) or the opencode CLI agent.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Model override for the selected backend. OpenRouter default: "
            f"{DEFAULT_OPENROUTER_MODEL}; opencode default: {DEFAULT_OPENCODE_MODEL}."
        ),
    )
    parser.add_argument("--opencode-server", default=None, help="opencode serve base URL, e.g. http://127.0.0.1:4096")
    parser.add_argument("--opencode-binary", default="opencode", help="opencode executable name/path.")
    parser.add_argument(
        "--no-auto-install",
        action="store_false",
        dest="auto_install",
        help="Do not download the opencode CLI if it is missing (it is downloaded via the official installer by default).",
    )
    parser.add_argument("--timeout", type=float, default=None, help="AI research timeout, in seconds (default: 300 OpenRouter / 900 opencode).")
    parser.add_argument("--no-ai", action="store_true", help="Skip the AI research step; just print normalized metadata.")
    parser.add_argument("--cache-path", default=None, help="SQLite cache path (default: ~/.cache/music-copyright-checker/cache.sqlite3).")
    parser.add_argument("--no-cache", action="store_true", help="Disable metadata and research caching.")
    parser.add_argument("--refresh", action="store_true", help="Ignore cached values and perform fresh lookups.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON output.")

    args = parser.parse_args(argv)

    timeout = args.timeout
    if timeout is None:
        timeout = DEFAULT_OPENROUTER_TIMEOUT if args.ai_backend == "openrouter" else DEFAULT_OPENCODE_TIMEOUT

    pipeline = Pipeline(
        ai_backend=args.ai_backend,
        ai_model=args.model,
        opencode_server=args.opencode_server,
        opencode_binary=args.opencode_binary,
        opencode_timeout=timeout,
        openrouter_timeout=timeout,
        run_ai_research=not args.no_ai,
        cache_enabled=not args.no_cache,
        cache_path=args.cache_path,
        opencode_auto_install=args.auto_install,
    )

    try:
        if args.spotify_url:
            result = pipeline.check_spotify_url(args.spotify_url, refresh=args.refresh)
        elif args.youtube_url:
            result = pipeline.check_youtube_url(args.youtube_url, refresh=args.refresh)
        else:
            result = pipeline.check_file(args.file, refresh=args.refresh)
    except MusicCheckerError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps(result.to_dict(), indent=2 if args.pretty else None, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
