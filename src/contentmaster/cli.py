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
    # 2026-09-19 (code scan): say out loud when the chosen channel has no
    # adapter. `--channel x` used to be the DEFAULT while `x` sat in
    # PLANNED_PLATFORMS, so the out-of-the-box run quietly produced a
    # simulated publish and nothing said so until the summary line.
    from .platforms.registry import PLATFORMS

    if args.channel not in PLATFORMS:
        print(f"[warn] '{args.channel}' has no implemented adapter yet — this run will go through "
              f"the whole loop but the publish step is a labeled simulation, not a real post. "
              f"Implemented now: {', '.join(PLATFORMS) or '(none)'}.")
    _pipeline.run(args.whitepaper, channel=args.channel, product_name=args.product_name,
                   features=args.features, n_posts=args.posts, terminal=args.terminal,
                   target_region=args.target_region, target_audience=args.target_audience)
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    return _pipeline.run_review(checkpoint=args.checkpoint, terminal=args.terminal)


def _cmd_status(args: argparse.Namespace) -> int:
    from .pending import print_status

    print_status()
    return 0


def _cmd_refresh(args: argparse.Namespace) -> int:
    return _pipeline.refresh_metrics()


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
        "--channel", default="bluesky",
        help="Target platform to publish to — see `contentmaster platforms` for what's "
             "implemented (default: bluesky, the only implemented adapter). A planned-but-"
             "unimplemented channel still runs the whole loop, with a simulated publish step.",
    )
    p_run.add_argument("--product-name", default=None)
    p_run.add_argument(
        "--features", nargs="+", default=None, metavar="FEATURE",
        help="What this product does, e.g. --features \"quiet indoors\" \"needs no walks\". Used to "
             "ground generation when there is no topic index yet, and by the last-resort "
             "template. Omit to use the built-in placeholder list — which is almost certainly "
             "not about YOUR product (it describes this pipeline itself).",
    )
    p_run.add_argument(
        "--posts", type=int, default=1,
        help="How many drafts to generate/review this run (default: 1 — kept small while "
             "still testing; each one gets its own text + possible image review)",
    )
    p_run.add_argument(
        "--terminal", action="store_true",
        help="Review drafts and images right here in the terminal instead of opening a "
             "browser (default: opens a browser page for review, see streamlit_app.py)",
    )
    p_run.add_argument(
        "--target-region", default=None,
        help="Who this product's posts should target, e.g. 'US' (testing phase: region only "
             "so far). Persisted as this product's default; omit to keep using whatever was "
             "set on a previous run, see topic_index.py",
    )
    p_run.add_argument(
        "--target-audience", default=None,
        help="Free text describing the target audience, e.g. 'cat owners in the US'. Same "
             "persistence behavior as --target-region.",
    )
    p_run.set_defaults(func=_cmd_run)

    p_review = sub.add_parser(
        "review",
        help="Check posts due for a checkpoint (24h/7d/30d) and run real analysis on matured metrics",
    )
    p_review.add_argument(
        "--checkpoint", default="24h", choices=["24h", "7d", "30d"],
        help="Which checkpoint tier to process (default: 24h — the only one wired up so far, "
             "see docs/SCHEDULE.md Phase 9)",
    )
    p_review.add_argument(
        "--terminal", action="store_true",
        help="Confirm each analysis right here in the terminal instead of opening a browser "
             "(default: opens a browser page for review, see streamlit_app.py's Analysis "
             "review tab)",
    )
    p_review.set_defaults(func=_cmd_review)

    p_refresh = sub.add_parser(
        "refresh",
        help="Re-read engagement for every tracked post under 30 days old (no analysis, "
             "no human decision — safe to run as often as you like)",
    )
    p_refresh.set_defaults(func=_cmd_refresh)

    p_status = sub.add_parser(
        "status",
        help="What needs a human right now: failed publishes, drafts and analyses waiting, "
             "and which errors keep repeating",
    )
    p_status.set_defaults(func=_cmd_status)

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
    _warn_if_isolated_data_root()
    # 2026-09-21: printed before every command, because the previous
    # notification surface was the stdout of a background Streamlit
    # process. A post failed to publish and nobody saw it for two days.
    # Silent when there is nothing pending — a banner that always prints
    # stops being read.
    if args.func is not _cmd_status:
        from .pending import print_startup_banner

        print_startup_banner()
    return args.func(args) or 0


def _warn_if_isolated_data_root() -> None:
    """Say it out loud when CONTENTMASTER_DATA_ROOT is redirecting writes.

    The whole point of the setting is that a test run leaves no trace in
    the real ledger, and the failure it guards against is the inverse:
    believing you are in test mode when you are not (which is how
    `ZZ_DISPOSABLE_TEST` ended up in plays/_history.jsonl). One line at
    the top of every command makes the current mode impossible to
    misread.
    """
    from .config import settings

    if settings.is_isolated_data_root:
        print(f"[test data root] Writing to {settings.data_root} — "
              "nothing this run does will touch the real ledger.\n")


if __name__ == "__main__":
    raise SystemExit(main())
