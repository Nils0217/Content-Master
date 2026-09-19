"""Pending analysis-confirmation queue: plays/_analysis_review.jsonl.

2026-09-18 (Phase 5 extended to `contentmaster review`, /grill-me design
session, see docs/LOG.md). Same problem draft_queue.py already solved for
draft review: Streamlit reruns its whole script on every click, it can't
block on input() the way analysis.confirm_with_human() does, so the "pull
metrics + analyze + get a recommendation" half and the "human confirms or
disagrees" half can't run in one synchronous call anymore for the browser
path. This file is the handoff point between them.

Different key than draft_queue.py's draft_id, though: the same post_id
can have three separate pending analyses over its life (24h, then later
7d, then 30d, see tracking_review.CHECKPOINT_MIN_AGE), so entries are
keyed on (post_id, checkpoint) together, not post_id alone. `entry` on
each record is tracking_review.py's own record for this post, carried
through so the decision-time handler (pipeline.apply_analysis_decision)
has everything tracking_review.mark_checkpoint_done() needs without
re-reading tracking_review.py itself.

Same append-only, last-write-wins pattern as draft_queue.py/
tracking_review.py/metrics_store.py: never edited in place, the current
state of one entry is just its most recently appended line.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .config import settings

QUEUE_PATH = settings.project_root / "plays" / "_analysis_review.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _key(post_id: str, checkpoint: str) -> str:
    return f"{post_id}:{checkpoint}"


def queue_analysis(
    post_id: str, checkpoint: str, entry: dict[str, Any],
    metrics: dict[str, Any], analysis: dict[str, Any], recommendation: str,
) -> None:
    """Called once per (post, checkpoint), right after the eager pull +
    analyze + synthesize step finishes (see pipeline._pull_and_analyze).
    `status` starts "pending"; a human decision in Streamlit appends an
    updated record with a new status, same pattern as
    draft_queue.queue_draft()/update_entry().
    """
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "post_id": post_id, "checkpoint": checkpoint, "entry": entry,
        "metrics": metrics, "analysis": analysis, "recommendation": recommendation,
        "status": "pending", "human_note": "", "ts": _now_iso(),
    }
    with QUEUE_PATH.open("a") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def _latest_entries() -> dict[str, dict[str, Any]]:
    if not QUEUE_PATH.exists():
        return {}
    latest: dict[str, dict[str, Any]] = {}
    with QUEUE_PATH.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            post_id, checkpoint = record.get("post_id"), record.get("checkpoint")
            if post_id and checkpoint:
                latest[_key(post_id, checkpoint)] = record
    return latest


def find_pending() -> list[dict[str, Any]]:
    """Every entry still at status "pending", in the order they were
    first queued.
    """
    entries = list(_latest_entries().values())
    entries.sort(key=lambda r: r.get("ts", ""))
    return [r for r in entries if r.get("status") == "pending"]


def find_pending_for(post_id: str, checkpoint: str) -> dict[str, Any] | None:
    """Used by pipeline.run_review() to skip a due post that's already
    sitting in this queue awaiting a human decision, regardless of
    whether *this* invocation is the terminal or the browser path (a
    /grill-me decision, 2026-09-18: without this check, re-running
    `contentmaster review` — in either mode — before the browser decision
    happens would re-pull metrics and re-analyze the same post/checkpoint
    a second time, and could double-capture it once the first decision
    finally comes in).
    """
    record = _latest_entries().get(_key(post_id, checkpoint))
    if record is not None and record.get("status") == "pending":
        return record
    return None


def update_entry(post_id: str, checkpoint: str, **fields: Any) -> dict[str, Any] | None:
    """Reads the latest state of one (post_id, checkpoint) entry, applies
    `fields` on top, and appends the merged result as the new latest
    state. Returns the merged record, or None if it was never queued.
    """
    current = _latest_entries().get(_key(post_id, checkpoint))
    if current is None:
        return None
    # `ts` stays the queue time (find_pending() sorts on it); `updated_at`
    # records when this state change happened — see the matching comment
    # in draft_queue.update_entry().
    updated = {**current, **fields, "updated_at": _now_iso()}
    with QUEUE_PATH.open("a") as fh:
        fh.write(json.dumps(updated, default=str) + "\n")
    return updated
