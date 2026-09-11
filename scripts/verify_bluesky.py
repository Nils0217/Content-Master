#!/usr/bin/env python3
"""Verify the Bluesky integration end to end.

Usage:
  ./.venv/bin/python scripts/verify_bluesky.py                       # connection test only
  ./.venv/bin/python scripts/verify_bluesky.py --search "AI agents"  # + fetch + Cognee ingest
  ./.venv/bin/python scripts/verify_bluesky.py --search "AI agents" --limit 3 --no-ingest

Reads BLUESKY_HANDLE / BLUESKY_APP_PASSWORD from the environment (or .env —
see env.example). Never prints the app password.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from automarketer.bluesky_client import (  # noqa: E402
    BlueskyAPIError,
    BlueskyAuthError,
    BlueskyClient,
    BlueskyConfigError,
    BlueskyRateLimitError,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--search", default=None, help="If set, fetch public posts matching this query")
    parser.add_argument("--limit", type=int, default=5, help="Number of posts to fetch (default 5)")
    parser.add_argument(
        "--no-ingest", action="store_true", help="Fetch posts but skip feeding them into Cognee"
    )
    args = parser.parse_args()

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
        print("\nNo --search given — connection test only. Pass --search \"<query>\" to also fetch posts.")
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
        from automarketer.cognee_client import CogneeClient

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


if __name__ == "__main__":
    raise SystemExit(main())
