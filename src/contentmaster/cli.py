"""Unified CLI entry point: `contentmaster <command>`.

Install once (from the project root, inside the venv):
    ./.venv/bin/pip install -e .
then `contentmaster` is on PATH as `./.venv/bin/contentmaster` (or bare
`contentmaster` once the venv is activated — `source .venv/bin/activate`).

Adding a new subcommand: write a `_cmd_*(args) -> int` function, register
its parser in build_parser(). Keep the actual logic in the relevant module
(pipeline.py, platforms/<name>.py, ...) — this file should stay a thin
argparse dispatcher, not grow real behavior of its own.

Adding a new platform (e.g. Mastodon, X, Instagram): write
platforms/<name>.py implementing the Platform interface (platforms/base.py),
register it in platforms/registry.py's PLATFORMS dict. No CLI change
needed — `connect`, `run --channel`, etc. all work with any registered
platform by name already.
"""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import pipeline as _pipeline


def _cmd_run(args: argparse.Namespace) -> int:
    _pipeline.run(args.whitepaper, channel=args.channel, product_name=args.product_name)
    return 0


def _cmd_platforms(args: argparse.Namespace) -> int:
    from .platforms.registry import PLANNED_PLATFORMS, PLATFORMS

    print("Implemented (usable now):")
    if PLATFORMS:
        for name in PLATFORMS:
            print(f"  - {name}")
    else:
        print("  (none)")
    print("\nPlanned (not implemented yet — see docs/SCHEDULE.md):")
    for name in PLANNED_PLATFORMS:
        print(f"  - {name}")
    return 0


def _cmd_connect(args: argparse.Namespace) -> int:
    from .platforms.base import PlatformAPIError, PlatformAuthError, PlatformConfigError, PlatformRateLimitError
    from .platforms.registry import get_platform

    try:
        platform = get_platform(args.platform)
    except PlatformConfigError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 1

    print("== Step 1: connection/auth test ==")
    try:
        identity = platform.test_connection()
    except PlatformConfigError as e:
        print(f"[FAIL] Configuration error: {e}", file=sys.stderr)
        return 2
    except PlatformAuthError as e:
        print(f"[FAIL] Authentication error: {e}", file=sys.stderr)
        return 3
    except PlatformRateLimitError as e:
        print(f"[FAIL] Rate limited: {e}", file=sys.stderr)
        return 4
    except PlatformAPIError as e:
        print(f"[FAIL] API error: {e}", file=sys.stderr)
        return 5

    print(f"[OK] Authenticated: {identity}")

    if not args.search:
        print(f'\nNo --search given — connection test only. Pass --search "<query>" to also fetch posts.')
        return 0

    print(f"\n== Step 2: fetch up to {args.limit} public post(s) matching '{args.search}' ==")
    try:
        posts = platform.fetch_public_posts(args.search, limit=args.limit)
    except NotImplementedError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 7
    except PlatformRateLimitError as e:
        print(f"[FAIL] Rate limited: {e}", file=sys.stderr)
        return 4
    except PlatformAPIError as e:
        print(f"[FAIL] API error: {e}", file=sys.stderr)
        return 5

    if not posts:
        print("[OK] Search succeeded but returned 0 posts — try a different --search term.")
        return 0

    print(f"[OK] Fetched {len(posts)} post(s):")
    for p in posts:
        preview = p.text.replace("\n", " ")[:80]
        print(f"  - @{p.author}: {preview!r} ({p.like_count} likes)")

    if args.no_ingest:
        print("\n--no-ingest set — skipping Cognee.")
        return 0

    print("\n== Step 3: ingest into Cognee (same pipeline as the whitepaper) ==")
    try:
        from .cognee_client import CogneeClient

        cognee = CogneeClient()
        texts = [p.as_text() for p in posts]
        result = cognee.add_raw_texts(texts, labels=args.platform)
        print(f"[OK] Cognee accepted {len(texts)} post(s): status={result.get('status')}")
        print("     Run cognify() (see pipeline.py) to fold these into the knowledge graph.")
    except Exception as e:  # noqa: BLE001 — Cognee being down shouldn't hide the platform fetch success above
        print(f"[WARN] Fetch succeeded but Cognee ingest failed: {e}", file=sys.stderr)
        print("       (Is the Cognee container running? See scripts/configure_cognee_llm.sh)", file=sys.stderr)
        return 6

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contentmaster", description="ContentMaster — compound marketing agent with real memory"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run the full extract -> draft -> review -> publish -> improve loop")
    p_run.add_argument("--whitepaper", required=True, help="Path to the source document")
    p_run.add_argument(
        "--channel", default="x",
        help="Target platform to publish to — see `contentmaster platforms` for what's implemented (default: x)",
    )
    p_run.add_argument("--product-name", default=None)
    p_run.set_defaults(func=_cmd_run)

    p_platforms = sub.add_parser("platforms", help="List implemented and planned platforms")
    p_platforms.set_defaults(func=_cmd_platforms)

    p_connect = sub.add_parser("connect", help="Test auth for a platform; optionally fetch + ingest posts")
    p_connect.add_argument("platform", help="Platform name, e.g. bluesky — see `contentmaster platforms`")
    p_connect.add_argument("--search", default=None, help="If set, fetch public posts matching this query")
    p_connect.add_argument("--limit", type=int, default=5, help="Number of posts to fetch (default 5)")
    p_connect.add_argument("--no-ingest", action="store_true", help="Fetch posts but skip feeding them into Cognee")
    p_connect.set_defaults(func=_cmd_connect)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
