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
from .opencode_go_client import DEFAULT_OPENCODE_GO_MODEL, DEFAULT_OPENCODE_GO_TIMEOUT
from .openai_compatible_client import DEFAULT_OPENAI_COMPAT_TIMEOUT
from .pipeline import Pipeline

AI_BACKENDS = ("openrouter", "opencode-go", "openai-compatible", "opencode")


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
        choices=AI_BACKENDS,
        default="openrouter",
        help="Primary AI backend: OpenRouter REST API (default), OpenCode Go REST API, "
        "a generic OpenAI-compatible endpoint, or the opencode CLI agent.",
    )
    parser.add_argument(
        "--fallback-ai-backend",
        dest="fallback_ai_backends",
        action="append",
        choices=AI_BACKENDS,
        default=None,
        metavar="BACKEND",
        help="Secondary AI backend tried if the primary fails. Repeat for a longer chain.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Model override for the primary backend. OpenRouter default: "
            f"{DEFAULT_OPENROUTER_MODEL}; OpenCode Go default: {DEFAULT_OPENCODE_GO_MODEL}; "
            f"opencode default: {DEFAULT_OPENCODE_MODEL}."
        ),
    )
    parser.add_argument(
        "--search-backend",
        choices=("auto", "server", "exa", "none"),
        default="auto",
        help="Web search mode: 'auto' uses the provider's server tool when available and "
        "Exa otherwise (default); 'server' forces openrouter:web_search; 'exa' forces the "
        "client-side Exa tool (needs EXA_API_KEY); 'none' disables web search.",
    )
    parser.add_argument(
        "--openai-compatible-base-url",
        default=None,
        help="Base URL for --ai-backend openai-compatible (default: OPENAI_COMPAT_BASE_URL).",
    )
    parser.add_argument(
        "--openai-compatible-model",
        default=None,
        help="Model for --ai-backend openai-compatible (default: OPENAI_COMPAT_MODEL).",
    )
    parser.add_argument(
        "--openai-compatible-api-key-env",
        default=None,
        help="Env var holding the key for the OpenAI-compatible endpoint (default: OPENAI_COMPAT_API_KEY).",
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
        if args.ai_backend == "openrouter":
            timeout = DEFAULT_OPENROUTER_TIMEOUT
        elif args.ai_backend == "opencode-go":
            timeout = DEFAULT_OPENCODE_GO_TIMEOUT
        elif args.ai_backend == "openai-compatible":
            timeout = DEFAULT_OPENAI_COMPAT_TIMEOUT
        else:
            timeout = DEFAULT_OPENCODE_TIMEOUT

    pipeline = Pipeline(
        ai_backend=args.ai_backend,
        ai_fallback_backends=args.fallback_ai_backends,
        ai_model=args.model,
        web_search_backend=args.search_backend,
        openai_compatible_base_url=args.openai_compatible_base_url,
        openai_compatible_model=args.openai_compatible_model,
        openai_compatible_api_key_env=args.openai_compatible_api_key_env,
        openai_compatible_timeout=timeout,
        opencode_server=args.opencode_server,
        opencode_binary=args.opencode_binary,
        opencode_timeout=timeout,
        openrouter_timeout=timeout,
        opencode_go_timeout=timeout,
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
