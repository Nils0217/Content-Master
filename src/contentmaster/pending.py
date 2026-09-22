"""What is waiting for a human right now.

2026-09-21. The failure this exists for: a post was rejected by Bluesky,
the warning was printed to the stdout of a background Streamlit process
nobody reads, the browser said `Published.`, and the operator found out
two days later by noticing the post was not on the account.

A warning nobody sees is not a warning. So the same facts are surfaced in
three places with different lifetimes: the review page shows it at the
moment it happens, every CLI command shows a one-line summary at startup
(the path you cannot avoid taking), and `contentmaster status` answers it
on demand.
"""
from __future__ import annotations

import json
from typing import Any

from . import analysis_queue, draft_queue, tracking_review
from .config import settings


def failed_publishes() -> list[dict[str, Any]]:
    """Drafts whose publish attempt failed and that nobody has dealt with."""
    return [e for e in draft_queue._latest_entries().values()
            if e.get("status") == "publish_failed"]


def awaiting_decision() -> list[dict[str, Any]]:
    """Analyses queued for a human verdict in the browser."""
    path = settings.data_root / "plays" / "_analysis_review.jsonl"
    if not path.exists():
        return []
    latest: dict[tuple, dict[str, Any]] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        latest[(r.get("post_id"), r.get("checkpoint"))] = r
    return [r for r in latest.values() if r.get("status") == "pending"]


def pending_drafts() -> list[dict[str, Any]]:
    return draft_queue.find_pending()


def summary_lines() -> list[str]:
    """One line per thing that needs a human. Empty when nothing does —
    the startup banner must stay silent on a clean run, or it becomes
    noise and stops being read.
    """
    lines = []
    failed = failed_publishes()
    if failed:
        kinds = {}
        for e in failed:
            kinds[e.get("failure_kind", "unknown")] = kinds.get(e.get("failure_kind", "unknown"), 0) + 1
        detail = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))
        lines.append(f"{len(failed)} draft(s) failed to publish and are still unresolved ({detail}).")
    waiting = awaiting_decision()
    if waiting:
        lines.append(f"{len(waiting)} analysis/analyses waiting for your confirm/disagree.")
    drafts = pending_drafts()
    if drafts:
        lines.append(f"{len(drafts)} draft(s) waiting for review.")
    return lines


def print_startup_banner() -> None:
    """Called once per CLI invocation, before the command runs."""
    lines = summary_lines()
    if not lines:
        return
    print("[pending]")
    for line in lines:
        print(f"  - {line}")
    print("  Run `contentmaster status` for detail.\n")


def print_status() -> None:
    """`contentmaster status` — the full picture."""
    failed = failed_publishes()
    print("=" * 62)
    print("FAILED PUBLISHES" if failed else "FAILED PUBLISHES: none")
    for e in failed:
        print(f"  {e.get('draft_id')} [{e.get('failure_kind')}] {e.get('product', '')}")
        print(f"     {e.get('failure_reason', '')}")
        print(f"     {(e.get('text') or '')[:90]}")

    waiting = awaiting_decision()
    print("\n" + ("AWAITING YOUR DECISION" if waiting else "AWAITING YOUR DECISION: none"))
    for e in waiting:
        print(f"  {e.get('post_id')} @ {e.get('checkpoint')}")

    drafts = pending_drafts()
    print("\n" + ("DRAFTS AWAITING REVIEW" if drafts else "DRAFTS AWAITING REVIEW: none"))
    for e in drafts:
        print(f"  {e.get('draft_id')} — {(e.get('text') or '')[:70]}")

    live = tracking_review.find_refreshable()
    print(f"\nTRACKED POSTS (live, under 30 days): {len(live)}")
    due = tracking_review.find_due("24h")
    if due:
        print(f"  {len(due)} due for a 24h checkpoint — run `contentmaster review`.")

    from .error_index import recurring_errors

    repeats = recurring_errors(min_count=2)
    print("\n" + ("REPEATED ERRORS (candidates to prevent, not just handle)"
                  if repeats else "REPEATED ERRORS: none"))
    for sig, info in repeats:
        print(f"  {info['count']:>3}x  {sig}")
        print(f"        last seen {info['last_seen'][:16]}")
    print("=" * 62)
