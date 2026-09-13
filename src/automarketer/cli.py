"""Unified CLI entry point: `automarketer <command>`.

Install once (from the project root, inside the venv):
    ./.venv/bin/pip install -e .
then `automarketer` is on PATH as `./.venv/bin/automarketer` (or bare
`automarketer` once the venv is activated — `source .venv/bin/activate`).

Adding a new subcommand: write a `_cmd_*(args) -> int` function, register
its parser in build_parser(). Keep the actual logic in the relevant module
(pipeline.py, bluesky_client.py, ...) — this file should stay a thin
argparse dispatcher, not grow real behavior of its own.
"""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import pipeline as _pipeline


def _cmd_run(args: argparse.Namespace) -> int:
    _pipeline.run(args.whitepaper, channel=args.channel, product_name=args.product_name)
    return 0


def _cmd_bluesky_verify(args: argparse.Namespace) -> int:
    from .bluesky_client import (
        BlueskyAPIError,
        BlueskyAuthError,
        BlueskyClient,
        BlueskyConfigError,
        BlueskyRateLimitError,
    )

    bsky = BlueskyClient()

    print("== Step 1: connection/auth test ==")
    try:
        profile = bsky.test_connection()
    except BlueskyConfigError as e:
        print(f"[FAIL] Configuration error: {e}", file=sys.stderr)
        return 2
    except BlueskyAuthError as e:
        print(f"[FAIL] Authentication error: {e}", file=sys.stderr)
        return 3
    except BlueskyRateLimitError as e:
        print(f"[FAIL] Rate limited: {e}", file=sys.stderr)
        return 4
    except BlueskyAPIError as e:
        print(f"[FAIL] API error: {e}", file=sys.stderr)
        return 5

    print(f"[OK] Authenticated as @{profile['handle']} (did={profile['did']})")

    if not args.search:
        print('\nNo --search given — connection test only. Pass --search "<query>" to also fetch posts.')
        return 0

    print(f"\n== Step 2: fetch up to {args.limit} public post(s) matching '{args.search}' ==")
    try:
        posts = bsky.fetch_public_posts(args.search, limit=args.limit)
    except BlueskyRateLimitError as e:
        print(f"[FAIL] Rate limited: {e}", file=sys.stderr)
        return 4
    except BlueskyAPIError as e:
        print(f"[FAIL] API error: {e}", file=sys.stderr)
        return 5

    if not posts:
        print("[OK] Search succeeded but returned 0 posts — try a different --search term.")
        return 0

    print(f"[OK] Fetched {len(posts)} post(s):")
    for p in posts:
        preview = p.text.replace("\n", " ")[:80]
        print(f"  - @{p.author_handle}: {preview!r} ({p.like_count} likes)")

    if args.no_ingest:
        print("\n--no-ingest set — skipping Cognee.")
        return 0

    print("\n== Step 3: ingest into Cognee (same pipeline as the whitepaper) ==")
    try:
        from .cognee_client import CogneeClient

        cognee = CogneeClient()
        texts = [p.as_text() for p in posts]
        result = cognee.add_raw_texts(texts, labels="bluesky")
        print(f"[OK] Cognee accepted {len(texts)} post(s): status={result.get('status')}")
        print("     Run cognify() (see pipeline.py) to fold these into the knowledge graph.")
    except Exception as e:  # noqa: BLE001 — Cognee being down shouldn't hide the Bluesky success above
        print(f"[WARN] Bluesky fetch succeeded but Cognee ingest failed: {e}", file=sys.stderr)
        print("       (Is the Cognee container running? See scripts/configure_cognee_llm.sh)", file=sys.stderr)
        return 6

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="automarketer", description="AutoMarketer — compound marketing agent with real memory"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run the full extract -> draft -> review -> publish -> improve loop")
    p_run.add_argument("--whitepaper", required=True, help="Path to the source document")
    p_run.add_argument("--channel", default="x", help="Target channel, e.g. x, bluesky (default: x)")
    p_run.add_argument("--product-name", default=None)
    p_run.set_defaults(func=_cmd_run)

    p_bluesky = sub.add_parser("bluesky", help="Bluesky-related commands")
    bluesky_sub = p_bluesky.add_subparsers(dest="bluesky_command", required=True)
    p_verify = bluesky_sub.add_parser("verify", help="Test Bluesky auth; optionally fetch + ingest posts")
    p_verify.add_argument("--search", default=None, help="If set, fetch public posts matching this query")
    p_verify.add_argument("--limit", type=int, default=5, help="Number of posts to fetch (default 5)")
    p_verify.add_argument("--no-ingest", action="store_true", help="Fetch posts but skip feeding them into Cognee")
    p_verify.set_defaults(func=_cmd_bluesky_verify)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
