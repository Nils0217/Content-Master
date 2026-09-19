"""Pending draft review queue: plays/_draft_review.jsonl.

2026-09-17 (Phase 5 design, see docs/LOG.md). Streamlit reruns its whole
script on every click, it has no way to block waiting on input() the way
human_loop.py does, so the generation step and the review step can't live
in one synchronous call anymore for the Streamlit path. This file is the
handoff point between them: `pipeline.py` writes drafts (with their
already-generated image, saved under generated_images/) here as soon as
they exist, then either reviews them right there in the terminal, or
leaves them here for streamlit_app.py to pick up and show in a browser.

Same append plus last write wins pattern already used by tracking_review.py
and metrics_store.py: never edited in place, the current state of one
entry is just its most recently appended line.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .config import settings

QUEUE_PATH = settings.project_root / "plays" / "_draft_review.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def queue_draft(
    draft_id: str, product: str, channel: str, text: str,
    topic: str = "", brief: str = "", image_path: str | None = None,
    source: str = "unknown",
) -> None:
    """Called once per draft, right after it (and its image, if any) is
    generated. `status` starts as "pending"; review actions append an
    updated record with a new status, same pattern as
    tracking_review.mark_checkpoint_done().

    `source` (2026-09-19, code scan) — "topics", "context" or "template",
    straight from draft_generator. Carried so the review UI can say out
    loud when a draft is the canned fallback rather than generated
    content; without it, the two were indistinguishable in the browser.

    `edited` starts False and is set by the review UI on a manual edit or
    an LLM revision. It used to be inferred at approve time by comparing
    the current text against the queued text, which broke as soon as a
    revision was written back to the queue (both sides then matched, so a
    genuinely edited post was recorded as untouched).
    """
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "draft_id": draft_id, "product": product, "channel": channel,
        "text": text, "topic": topic, "brief": brief, "image_path": image_path,
        "source": source, "status": "pending", "reviewer_note": "",
        "edited": False, "ts": _now_iso(),
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
            draft_id = record.get("draft_id")
            if draft_id:
                latest[draft_id] = record
    return latest


def find_pending() -> list[dict[str, Any]]:
    """Every entry still at status "pending", in the order they were
    first queued.
    """
    entries = list(_latest_entries().values())
    entries.sort(key=lambda r: r.get("ts", ""))
    return [r for r in entries if r.get("status") == "pending"]


def update_entry(draft_id: str, **fields: Any) -> dict[str, Any] | None:
    """Reads the latest state of one entry, applies `fields` on top, and
    appends the merged result as the new latest state. Returns the merged
    record, or None if `draft_id` was never queued.
    """
    current = _latest_entries().get(draft_id)
    if current is None:
        return None
    # 2026-09-19 (code scan): `{**current, **fields}` copies the ORIGINAL
    # `ts` forward, so every appended state change carried the time the
    # draft was queued, never the time the decision was made — the file
    # could not answer "when was this approved?" at all. `ts` stays the
    # queue time on purpose (find_pending() sorts on it, and re-stamping
    # it would reorder the list under a reviewer mid-session); the new
    # `updated_at` is the one that moves.
    updated = {**current, **fields, "updated_at": _now_iso()}
    with QUEUE_PATH.open("a") as fh:
        fh.write(json.dumps(updated, default=str) + "\n")
    return updated
