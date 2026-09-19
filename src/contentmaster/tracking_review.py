"""Layer between publish and analysis — plays/_tracking_review.jsonl.

Design session: /grill-me, 2026-09-15 (see docs/SCHEDULE.md Phase 9,
docs/LOG.md for the full Q&A). The problem this replaces: `pipeline.py`
used to pull metrics and run analyze+discuss+capture *seconds* after
publish, when a post has had no real time to be seen by anyone
(impressions=1, ctr=0.0 is a real example, not a bug) — so every "trend"
and "did the last suggestion help" verdict was computed from noise.

Now: publish just writes an entry here (proof it really posted — no
separate metrics pull needed for that). A later, separate step —
`contentmaster review` (see cli.py/pipeline.py's run_review()) — comes
back once real time has passed and does the actual analysis, at one of
3 checkpoints:

    24h  — did the algorithm even push this out?
    7d   — is the heat sustained? (only 24h is built as of 2026-09-15;
    30d  — long-term, monthly batch review   7d/30d follow once 24h is
                                              proven out in production)

Same append-only JSONL pattern as metrics_store.py: never edited in
place, "the record's current state" is just its most-recently-appended
line for that post_id (see _latest_entries()). This file is genuinely
mutable state (checkpoints_done changes over a post's life), unlike
plays/_history.jsonl / _failures.jsonl / audit/events.jsonl, which are
logs of things that already, permanently, happened — re-appending the
whole record here (rather than editing a line in place) keeps the same
"never seek into the middle of the file" simplicity those files have.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import settings

TRACKING_PATH = settings.project_root / "plays" / "_tracking_review.jsonl"

# How long after `ts` each checkpoint becomes due. Only "24h" has a
# processing function wired up in pipeline.py yet (see docs/SCHEDULE.md
# Phase 9 — 24h was deliberately built first, to prove the mechanism
# works in production before waiting a full week for 7d).
CHECKPOINT_MIN_AGE: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
# Highest-maturity first — this order is what draft_generator's fallback
# (modiqo_play.find_best_prior) prefers when picking which post's
# analysis to build the next draft on.
CHECKPOINT_MATURITY_ORDER: tuple[str, ...] = ("30d", "7d", "24h")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def queue_for_review(
    post_id: str, product: str, channel: str, post_ref: str | None,
    final_text: str, edited: bool, reviewer_note: str,
) -> None:
    """Called once, right after a successful publish. Replaces the old
    immediate metrics-pull/Modiqo-capture calls (step6_track_metrics /
    step7_modiqo_capture, both removed 2026-09-15) — this is
    the entire synchronous-publish-time footprint now.
    """
    TRACKING_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "post_id": post_id,
        "product": product,
        "channel": channel,
        "post_ref": post_ref,
        "final_text": final_text,
        "ts": _now_iso(),
        "edited": edited,
        "reviewer_note": reviewer_note,
        "checkpoints_done": {},
    }
    with TRACKING_PATH.open("a") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def _latest_entries() -> dict[str, dict[str, Any]]:
    """Every post_id's most-recently-appended record — "last write wins",
    same read pattern as metrics_store.get_metrics().
    """
    if not TRACKING_PATH.exists():
        return {}
    latest: dict[str, dict[str, Any]] = {}
    with TRACKING_PATH.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            post_id = record.get("post_id")
            if post_id:
                latest[post_id] = record
    return latest


def find_due(checkpoint: str) -> list[dict[str, Any]]:
    """Posts whose `ts` is at least CHECKPOINT_MIN_AGE[checkpoint] in the
    past and haven't had this checkpoint processed yet. No upper window —
    a post due 4 days ago (the CLI wasn't run in time) is still due, just
    late; see the `deviation` note on mark_checkpoint_done for how that
    lateness is recorded rather than silently ignored.
    """
    min_age = CHECKPOINT_MIN_AGE[checkpoint]
    now = datetime.now(timezone.utc)
    due = []
    for record in _latest_entries().values():
        if checkpoint in (record.get("checkpoints_done") or {}):
            continue
        try:
            published_ts = datetime.fromisoformat(record["ts"])
        except (KeyError, ValueError):
            continue
        if now - published_ts >= min_age:
            due.append(record)
    return due


def mark_checkpoint_done(record: dict[str, Any], checkpoint: str) -> None:
    """Re-appends `record` with `checkpoint` added to checkpoints_done,
    stamped with the actual run time (not just "done") — so analysis can
    later tell a checkpoint that ran exactly on schedule from one the
    `contentmaster review` command just happened to be run late for, and
    correct for the deviation instead of assuming it was on-time.
    """
    updated = {**record}
    checkpoints_done = dict(updated.get("checkpoints_done") or {})
    checkpoints_done[checkpoint] = _now_iso()
    updated["checkpoints_done"] = checkpoints_done
    with TRACKING_PATH.open("a") as fh:
        fh.write(json.dumps(updated, default=str) + "\n")
