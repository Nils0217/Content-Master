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


def _resolve_whitepaper(given: str | None) -> str | None:
    """A full path, a bare filename in the whitepapers folder, or nothing
    when that folder holds exactly one document. Says what it could not
    resolve rather than failing later with an unreadable path — a run that
    cannot read its source does not stop, it falls back to a cached index
    and quietly writes about the previous version of the document.
    """
    from . import folders

    resolved = folders.resolve_document(given)
    if resolved is not None:
        return str(resolved)
    docs = folders.documents()
    folder = folders.get("whitepapers")
    if given:
        print(f"[error] Could not find {given!r}, either as a path or inside {folder}.")
    elif not docs:
        print(f"[error] No source document given, and {folder} holds none. "
              "Put one there, or point somewhere else with `contentmaster folders "
              "--whitepapers PATH`.")
    else:
        print(f"[error] No source document given, and {folder} holds {len(docs)}. "
              "Name one with --whitepaper:")
        for d in docs:
            print(f"          {d.name}")
    return None


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
    whitepaper = _resolve_whitepaper(args.whitepaper)
    if whitepaper is None:
        return 1
    if args.cluster:
        return _pipeline.run_cluster(whitepaper, channel=args.channel,
                                     product_name=args.product_name, terminal=args.terminal,
                                     no_image=args.no_image)
    _pipeline.run(whitepaper, channel=args.channel, product_name=args.product_name,
                   features=args.features, n_posts=args.posts, terminal=args.terminal,
                   target_region=args.target_region, target_audience=args.target_audience,
                   no_image=args.no_image)
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    from . import scheduler

    if args.unschedule:
        print(f"Mac: {scheduler.unschedule_review()}")
        return 0
    if args.schedule:
        try:
            print(f"Mac: {scheduler.schedule_review(args.schedule)}")
        except ValueError as e:
            print(f"[error] {e}")
            return 1
        print("Analyses are made at that time and wait for you. Nothing is confirmed or published "
              "for you; a notification says when one is ready.")
        return 0
    return _pipeline.run_review(checkpoint=args.checkpoint, terminal=args.terminal)


def _cmd_review_due(args: argparse.Namespace) -> int:
    from . import scheduler

    scheduler.review_due()
    return 0


def _cmd_images(args: argparse.Namespace) -> int:
    from . import product_assets, product_registry

    names = sorted(product_registry.known_names(),
                   key=lambda n: product_registry.lookup(n).get("last_used", ""))
    product = args.product_name or (names[-1] if names else None)
    if product is None:
        print("[error] No product yet. Pass --product-name.")
        return 1
    if args.source:
        product_assets.set_image_source(product, args.source)
    print(f"{product}: {product_assets.IMAGE_SOURCES[product_assets.image_source(product)]}. "
          f"Your files: {product_assets.describe(product)['count']}.")
    return 0


def _cmd_errors(args: argparse.Namespace) -> int:
    from . import error_index

    rows = error_index.report(args.min_count)
    picked = args.propose or args.accept or args.reject
    if picked:
        if not 1 <= picked <= len(rows):
            print(f"[error] There is no error number {picked}; the list has {len(rows)}.")
            return 1
        sig = rows[picked - 1]["signature"]
        try:
            if args.propose:
                print(f"Asking {error_index.FIX_MODEL}; this uses Ollama and can take a few minutes.")
                if error_index.propose_fix(sig, args.min_count) is None:
                    print("[error] The model did not answer. Why is in the audit log (model_error).")
                    return 1
            else:
                error_index.decide_proposal(sig, bool(args.accept), args.note)
        except KeyError as e:
            print(f"[error] {e.args[0]}")
            return 1
        rows = error_index.report(args.min_count)
    if not rows:
        print("No errors recorded.")
    for i, r in enumerate(rows, start=1):
        print(f"{i:>3}. {r['count']}x  {r['signature']}")
        print(f"     first {r['first_seen'][:16]}, last {r['last_seen'][:16]}"
              + (f", kind {r['kind']}" if r["kind"] else ""))
        for heading, _ in r["known"]:
            print(f"     past write-up: {heading}")
        if r["proposal"]:
            print(f"     proposal ({r['proposal']['status']}, {r['proposal']['model']}):")
            print("       " + r["proposal"]["raw"].replace("\n", "\n       "))
    return 0


def _cmd_rules(args: argparse.Namespace) -> int:
    from . import platform_rules

    try:
        if args.add:
            r = platform_rules.add(args.platform, args.add, args.evidence)
            print(f"Added {r['id']}.")
        if args.retire:
            platform_rules.retire(args.retire)
            print(f"Retired {args.retire}.")
    except (ValueError, KeyError) as e:
        print(f"[error] {e.args[0]}")
        return 1
    rules = platform_rules.active(args.platform if not args.add else None)
    if not rules:
        print("No platform rules.")
    for r in rules:
        print(f"  {r['id']}  {r['platform']}: {r['rule']}\n            evidence: {r['evidence']}")
    return 0


def _cmd_video(args: argparse.Namespace) -> int:
    whitepaper = _resolve_whitepaper(args.whitepaper)
    if whitepaper is None:
        return 1
    return _pipeline.run_video(
        whitepaper, channel=args.channel, product_name=args.product_name,
        features=args.features, n_posts=args.posts,
        target_region=args.target_region, target_audience=args.target_audience)


def _cmd_folders(args: argparse.Namespace) -> int:
    from . import folders

    changed = False
    for kind, value in (("whitepapers", args.whitepapers), ("external", args.external)):
        if not value:
            continue
        try:
            resolved = folders.set_folder(kind, value)
        except (NotADirectoryError, ValueError) as e:
            print(f"[error] {e}")
            return 1
        print(f"{kind}: now {resolved}")
        changed = True

    described = folders.describe()
    if changed:
        print()
    for kind, info in described.items():
        files = info["files"]
        label = "source documents" if kind == "whitepapers" else "external data files"
        print(f"{kind:<12} {info['path']}")
        if not info["path"].is_dir():
            print(f"{'':<12}   (folder does not exist)")
        elif not files:
            print(f"{'':<12}   (no {label} in it)")
        else:
            for f in files:
                print(f"{'':<12}   {f.name}")
    if not changed:
        print("\nSet one with:  contentmaster folders --whitepapers \"Some folder\"")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    from .pending import print_status

    print_status()
    return 0


def _cmd_clusters(args: argparse.Namespace) -> int:
    """Every cluster and the posts in it. Backfills cluster 0 first."""
    from . import clusters, posts

    added = posts.backfill_from_tracking()
    if added:
        print(f"[backfill] {added} post(s) published before clusters existed → cluster 0.\n")
    by_product = sorted({(c["product"], c["channel"]) for c in clusters.all_clusters().values()})
    if not by_product:
        print("No clusters yet.")
        return 0
    for product, channel in by_product:
        for c in clusters.for_product(product, channel):
            cid = c["cluster_id"]
            if cid == 0:
                head = "cluster 0  (before clusters existed — tests nothing)"
            else:
                head = (f"cluster {cid}  week of {c['week_start']} ({clusters.month_of(c)})  "
                        f"{c['status']}  experiment days: {', '.join(c['experiment_weekdays'])}")
            print(f"{product} / {channel} — {head}")
            if c.get("hypothesis"):
                print(f"    hypothesis: {c['hypothesis'].get('test', '')}  "
                      f"(watch {c['hypothesis'].get('watch') or '—'})")
            for r in posts.in_cluster(product, channel, cid):
                nid = f"#{r['natural_id']}" if r.get("natural_id") else "#—"
                role = ("extra" if r["kind"] == "extra" else
                        "experiment" if r.get("experiment") else
                        "main" if r.get("experiment") is False else "")
                when = r.get("published_at") or r.get("scheduled_at") or r.get("scheduled_for") or ""
                print(f"    {nid:>4}  {r['status']:<9} {role:<10} {str(when)[:16]:<16} {r['post_id']}")
            print()
    return 0


def _cmd_schedule(args: argparse.Namespace) -> int:
    """Terminal counterpart of the review page's Schedule tab."""
    from . import clusters, scheduler

    # No product name typed: the product of the most recently touched
    # cluster on this channel — never a name written into the code.
    live = sorted((c for c in clusters.all_clusters().values()
                   if c["cluster_id"] > 0 and c["channel"] == args.channel
                   and (not args.product_name or c["product"] == args.product_name)),
                  key=lambda c: c.get("seq") or 0)
    if not live:
        print("No cluster yet — run `contentmaster run --cluster` first.")
        return 1
    if args.cluster is not None:
        product = live[-1]["product"]
        cid = args.cluster
    else:
        chosen = live[scheduler.default_cluster(live)]
        product, cid = chosen["product"], chosen["cluster_id"]
    result = scheduler.schedule_cluster(product, args.channel, cid, args.time)
    print(f"Cluster {cid}: {len(result['scheduled'])} post(s) scheduled at {args.time} "
          f"({', '.join(result['scheduled']) or 'none'}).")
    if result["not_approved"]:
        print(f"  Not approved yet: {', '.join(result['not_approved'])} — they join this time "
              "when you approve them.")
    if result["past"]:
        print(f"  Already past: {', '.join(result['past'])} — published at the next wake, marked late.")
    print(f"  Mac: {scheduler.sync_agent()}")
    return 0


def _cmd_publish_due(args: argparse.Namespace) -> int:
    from . import scheduler

    n = scheduler.publish_due()
    print(f"[publish-due] {n} post(s) published.")
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
    p_run.add_argument(
        "--whitepaper", default=None,
        help="Source document. A full path, or just the filename inside the whitepapers "
             "folder (see `contentmaster folders`). Omit it when that folder holds exactly "
             "one document.",
    )
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
             "ground generation when there is no topic index yet. If nothing can ground a draft, "
             "the run produces no drafts rather than inventing them.",
    )
    p_run.add_argument(
        "--posts", type=int, default=1,
        help="How many drafts to generate/review this run (default: 1 — kept small while "
             "still testing; each one gets its own text + possible image review)",
    )
    p_run.add_argument(
        "--no-image", action="store_true",
        help="Skip image generation entirely. An image is made for every draft before you see "
             "it — the browser cannot pause mid-review to make one — so a run you expect to "
             "reject costs a generation per draft either way. Use this while iterating on "
             "wording. Recorded as a 'no image' characteristic, so whether images help stays "
             "answerable later.",
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
    p_run.add_argument(
        "--cluster", action="store_true",
        help="Draft next week's hypothesis cluster: seven posts dated Monday to Sunday, five "
             "making the week's change and two (the experiment days) deliberately not. "
             "Approving one does not publish it. Run again to fill days left empty. Without "
             "this flag, --posts N drafts extra posts that publish on approval.",
    )
    p_run.set_defaults(func=_cmd_run)

    p_review = sub.add_parser(
        "review",
        help="Check posts due for a checkpoint (24h/7d/30d) and run real analysis on matured metrics",
    )
    p_review.add_argument(
        "--checkpoint", default="24h", choices=["24h", "7d", "30d"],
        help="Which checkpoint tier to process (default: 24h)",
    )
    p_review.add_argument(
        "--terminal", action="store_true",
        help="Confirm each analysis right here in the terminal instead of opening a browser "
             "(default: opens a browser page for review, see streamlit_app.py's Analysis "
             "review tab)",
    )
    p_review.add_argument(
        "--schedule", nargs="?", const="18:30", default=None, metavar="HH:MM",
        help="Have the Mac analyse whatever is due once a day at this time (default 18:30), up to "
             "three posts a day. You still confirm each one here or in the browser.",
    )
    p_review.add_argument("--unschedule", action="store_true", help="Turn the daily review off")
    p_review.set_defaults(func=_cmd_review)

    p_review_due = sub.add_parser(
        "review-due", help="Analyse what is due, without opening anything (what the Mac runs)")
    p_review_due.set_defaults(func=_cmd_review_due)

    p_images = sub.add_parser(
        "images", help="Where a product's images come from on the next run (same as the review page)")
    p_images.add_argument("--source", choices=["auto", "generate", "supplied"], default=None,
                          help="auto: your files if any, else generate. generate: always generate. "
                               "supplied: only your files, never call the image model.")
    p_images.add_argument("--product-name", default=None, help="Default: the most recently used product")
    p_images.set_defaults(func=_cmd_images)

    p_errors = sub.add_parser(
        "errors", help="Every recorded error, with past write-ups and any proposed fix "
                       "(same list as the review page's Errors tab)")
    p_errors.add_argument("--min-count", type=int, default=1, help="Only errors seen this many times")
    p_errors.add_argument("--propose", type=int, metavar="N", help="Ask a local model for a fix to error N")
    p_errors.add_argument("--accept", type=int, metavar="N", help="Accept the proposal on error N")
    p_errors.add_argument("--reject", type=int, metavar="N", help="Reject the proposal on error N")
    p_errors.add_argument("--note", default="", help="Your note on an accept or reject")
    p_errors.set_defaults(func=_cmd_errors)

    p_rules = sub.add_parser(
        "rules", help="What is verified about a platform, for every product on it. Only people add these.")
    p_rules.add_argument("--platform", default=None, help="e.g. bluesky")
    p_rules.add_argument("--add", default=None, metavar="RULE", help="Add a rule; needs --platform and --evidence")
    p_rules.add_argument("--evidence", default="", help="What shows this holds for the platform, not one product")
    p_rules.add_argument("--retire", default=None, metavar="ID", help="Stop using a rule; it stays on record")
    p_rules.set_defaults(func=_cmd_rules)

    p_refresh = sub.add_parser(
        "refresh",
        help="Re-read engagement for every tracked post under 30 days old (no analysis, "
             "no human decision — safe to run as often as you like)",
    )
    p_refresh.set_defaults(func=_cmd_refresh)

    p_video = sub.add_parser(
        "video",
        help="Draft a post and compose a short video for it (script -> images -> speech -> "
             "ffmpeg), then review it in the browser",
    )
    p_video.add_argument(
        "--whitepaper", default=None,
        help="Source document; same resolution as `run` (see `contentmaster folders`).",
    )
    p_video.add_argument("--channel", default="bluesky", help="Target platform (default: bluesky)")
    p_video.add_argument("--product-name", default=None)
    p_video.add_argument("--features", nargs="+", metavar="FEATURE", default=None,
                          help="What this product does — same as `run`")
    p_video.add_argument("--posts", type=int, default=1,
                          help="How many drafts to make videos for (default: 1 — each one costs "
                               "several image generations and about half a minute)")
    p_video.add_argument("--target-region", default=None)
    p_video.add_argument("--target-audience", default=None)
    p_video.set_defaults(func=_cmd_video)

    p_folders = sub.add_parser(
        "folders",
        help="Show or set where source documents and external data live. Run with no "
             "arguments to see the current folders and what is in them.",
    )
    p_folders.add_argument(
        "--whitepapers", metavar="PATH", default=None,
        help="Folder holding the source documents. Also decides the Cognee dataset, so "
             "documents for different products belong in different folders.",
    )
    p_folders.add_argument(
        "--external", metavar="PATH", default=None,
        help="Folder holding external data (Google Trends exports and similar) that dbt "
             "loads as seeds.",
    )
    p_folders.set_defaults(func=_cmd_folders)

    p_status = sub.add_parser(
        "status",
        help="What needs a human right now: failed publishes, drafts and analyses waiting, "
             "and which errors keep repeating",
    )
    p_status.set_defaults(func=_cmd_status)

    p_schedule = sub.add_parser(
        "schedule", help="Schedule a cluster's approved posts at one time of day (same as the "
                         "review page's Schedule tab)")
    p_schedule.add_argument("--time", required=True, help="HH:MM, half-hour steps 06:00–23:30")
    p_schedule.add_argument("--cluster", type=int, default=None, help="Default: the latest cluster")
    p_schedule.add_argument("--product-name", default=None)
    p_schedule.add_argument("--channel", default="bluesky")
    p_schedule.set_defaults(func=_cmd_schedule)

    p_publish_due = sub.add_parser(
        "publish-due", help="Publish scheduled posts whose time has come (what the Mac runs)")
    p_publish_due.set_defaults(func=_cmd_publish_due)

    p_clusters = sub.add_parser(
        "clusters", help="List hypothesis clusters and the posts in each (natural id, role, status)")
    p_clusters.set_defaults(func=_cmd_clusters)

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
