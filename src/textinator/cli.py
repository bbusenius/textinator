"""textinator CLI — turn any text into a podcast episode.

Examples:
    textinator note.txt
    textinator https://example.com/article
    textinator book.epub                      # m4b with chapters
    cat article.txt | textinator - --voice en-US-AriaNeural
    textinator note.txt --backend grok --dry-run
    textinator --list-voices en

Subcommands:
    textinator auth xai [--no-browser]                 connect xAI OAuth
    textinator auth status                             show xAI auth sources
    textinator auth logout                             remove Textinator OAuth
    textinator serve [--feed-dir feed] [--port 8080]   serve the feed on the LAN
    textinator web   [--feed-dir feed] [--port 8765]   paste-from-phone web UI + feed
    textinator sync DEST [--feed-dir feed] [--base-url URL]   copy/rsync the feed
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .backends import AVAILABLE_BACKENDS, get_backend
from .pipeline import BudgetExceeded, dry_run, run

DEFAULT_FEED_DIR = Path("feed")
DEFAULT_PAID_BUDGET = 50_000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="textinator",
        description="Turn any text into an audiobook episode in a private podcast feed.",
        epilog=__doc__.split("Examples:")[1] if "Examples:" in (__doc__ or "") else None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="text file, URL (http/https), .epub, or '-' (or nothing, when "
        "piped) for stdin",
    )
    parser.add_argument(
        "--backend",
        choices=AVAILABLE_BACKENDS,
        default="edge",
        help="TTS engine (default: edge — free)",
    )
    parser.add_argument("--voice", help="voice name (backend-specific default)")
    parser.add_argument("--title", help="episode title (default: first line of text)")
    parser.add_argument(
        "--feed-dir",
        type=Path,
        default=DEFAULT_FEED_DIR,
        help=f"feed output directory (default: ./{DEFAULT_FEED_DIR})",
    )
    parser.add_argument(
        "--base-url",
        help="public base URL the feed will be served from "
        "(stored in the feed; only needed once or to change it)",
    )
    parser.add_argument("--cache-dir", type=Path, help="audio cache directory")
    parser.add_argument(
        "--format",
        choices=("mp3", "m4a", "m4b"),
        help="episode format (default: m4b when the source has chapters, "
        "else mp3; m4a/m4b carry chapter markers)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report chunk/char counts and cost type without synthesizing",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_PAID_BUDGET,
        help="per-run character budget for paid backends "
        f"(default: {DEFAULT_PAID_BUDGET:,})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="override the paid-backend character budget",
    )
    parser.add_argument(
        "--xai-auth",
        choices=("oauth", "api"),
        help="xAI credential source for Grok (default: oauth)",
    )
    parser.add_argument(
        "--api-fallback",
        action="store_true",
        help="allow Grok to use the metered API key if configured OAuth fails",
    )
    parser.add_argument(
        "--list-voices",
        nargs="?",
        const="",
        metavar="LANG",
        help="list voices for the backend, optionally filtered by language prefix",
    )
    parser.add_argument(
        "--version", action="version", version=f"textinator {__version__}"
    )
    return parser


def _cmd_list_voices(backend_name: str, prefix: str) -> int:
    if backend_name == "edge":
        from .backends.edge import EdgeBackend

        for voice in EdgeBackend.list_voices(prefix or None):
            print(f"{voice['ShortName']:40s} {voice['Gender']:8s} {voice['Locale']}")
        return 0
    backend = get_backend(backend_name)
    print(f"default voice for {backend_name}: {backend.default_voice}")
    return 0


def _cmd_serve(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="textinator serve")
    parser.add_argument("--feed-dir", type=Path, default=DEFAULT_FEED_DIR)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--url-host",
        help="hostname/IP to use in printed URLs and the feed "
        "(default: auto-detected LAN address)",
    )
    args = parser.parse_args(argv)
    from .deliver import serve

    try:
        serve(args.feed_dir, port=args.port, url_host=args.url_host)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def _cmd_web(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="textinator web")
    parser.add_argument("--feed-dir", type=Path, default=DEFAULT_FEED_DIR)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--url-host",
        help="hostname/IP to use in printed URLs, the QR code, and the feed "
        "(default: auto-detected LAN address)",
    )
    args = parser.parse_args(argv)
    from .webui import run_server

    run_server(args.feed_dir, port=args.port, url_host=args.url_host)
    return 0


def _cmd_sync(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="textinator sync")
    parser.add_argument("dest", help="local dir or rsync target (host:path)")
    parser.add_argument("--feed-dir", type=Path, default=DEFAULT_FEED_DIR)
    parser.add_argument("--base-url", help="rewrite feed.xml for this URL first")
    args = parser.parse_args(argv)
    from .deliver import sync

    try:
        sync(args.feed_dir, args.dest, base_url=args.base_url)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_auth(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="textinator auth")
    parser.add_argument("action", choices=("xai", "status", "logout"))
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="print the xAI verification URL without opening a browser",
    )
    args = parser.parse_args(argv)
    from .xai_auth import XAIAuthError, auth_status, oauth_login, oauth_logout

    try:
        if args.action == "xai":
            oauth_login(open_browser=not args.no_browser)
            print("xAI OAuth: connected")
            return 0
        if args.action == "logout":
            removed = oauth_logout()
            print("xAI OAuth: removed" if removed else "xAI OAuth: not configured")
            return 0
        status = auth_status()
    except XAIAuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4

    print(f"OAuth:  {'configured' if status['oauth'] else 'not configured'}")
    print(f"API key: {'available' if status['api'] else 'not set'}")
    default = status["default"]
    print(
        "default: "
        + ("OAuth" if default == "oauth" else "API key")
    )
    return 0


_SUBCOMMANDS = {
    "auth": _cmd_auth,
    "serve": _cmd_serve,
    "web": _cmd_web,
    "sync": _cmd_sync,
}


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] in _SUBCOMMANDS:
        return _SUBCOMMANDS[argv[0]](argv[1:])
    args = build_parser().parse_args(argv)

    if args.list_voices is not None:
        return _cmd_list_voices(args.backend, args.list_voices)

    if args.source is None and sys.stdin.isatty():
        print(
            "error: give a text file, or pipe text in (textinator -)",
            file=sys.stderr,
        )
        return 2

    from .inputs import epub, paste, pdf, url

    try:
        if args.source and url.is_url(args.source):
            document = url.load(args.source)
        elif args.source and epub.is_epub(args.source):
            document = epub.load(args.source)
        elif args.source and pdf.is_pdf(args.source):
            document = pdf.load(args.source)
        else:
            document = paste.load(args.source)
    except (FileNotFoundError, ValueError, url.ExtractionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.title:
        document.title = args.title

    episode_format = args.format or ("m4b" if document.chapters else "mp3")
    backend = get_backend(args.backend)
    if backend.name == "grok":
        backend.auth_mode = args.xai_auth
        backend.allow_api_fallback = args.api_fallback

    if args.dry_run:
        report = dry_run(document, backend, args.voice, args.cache_dir)
        print(f"title:            {document.title}")
        print(f"backend:          {backend.name}")
        print(f"chunks:           {report.chunk_count}")
        print(f"total chars:      {report.char_count:,}")
        print(f"cached chunks:    {report.cached_chunks}")
        print(f"chars to send:    {report.chars_to_synthesize:,}")
        print(f"cost:             {backend.cost_label}")
        return 0

    if backend.name == "grok":
        from .backends.grok import GrokError

        try:
            backend.prepare_auth()
        except GrokError as exc:
            can_prompt = (
                backend.api_fallback_available
                and not args.api_fallback
                and args.xai_auth != "api"
                and sys.stdin.isatty()
            )
            if not can_prompt:
                print(f"error: {exc}", file=sys.stderr)
                return 4
            answer = input("OAuth unavailable. Use metered API? [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                print("cancelled", file=sys.stderr)
                return 4
            backend.use_api_key()
            try:
                backend.prepare_auth()
            except GrokError as api_exc:
                print(f"error: {api_exc}", file=sys.stderr)
                return 4
        print(f"xAI auth: {backend.auth_label}", file=sys.stderr)
        print(f"cost: {backend.cost_label}", file=sys.stderr)

    try:
        result = run(
            document,
            backend,
            feed_dir=args.feed_dir,
            voice=args.voice,
            base_url=args.base_url,
            cache_dir=args.cache_dir,
            max_chars_budget=args.max_chars if backend.paid else None,
            force=args.force,
            episode_format=episode_format,
            on_progress=lambda msg: print(f"  {msg}", file=sys.stderr),
        )
    except BudgetExceeded as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        if backend.name != "grok":
            raise
        from .backends.grok import GrokError

        if not isinstance(exc, GrokError):
            raise
        print(f"error: {exc}", file=sys.stderr)
        return 4

    minutes, seconds = divmod(int(result.duration_seconds), 60)
    print(f"episode: {result.episode_path}")
    print(f"feed:    {result.feed_xml_path}")
    print(
        f"({result.chunk_count} chunks, {result.chunks_synthesized} synthesized, "
        f"{result.char_count:,} chars, {minutes}m{seconds:02d}s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
