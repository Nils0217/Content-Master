"""One row per post: plays/_posts.jsonl.

2026-10-06. Before this no table had one row per post. A post's state was
spread across the draft queue, the tracking queue and history, each keyed
by `draft-xxxxxxxx`, and "post 12" or "the posts in week 3" could not be
said at all. This ledger is the post's identity and its place in an
experiment; content and metrics stay where they already live.

  natural_id   1, 2, 3 …   per product × channel; given at APPROVAL, so a
                           rejected draft never consumes a number and the
                           sequence is exactly the posts that go out
  post_id      UUID        the system's key (legacy posts keep `draft-…`)
  cluster_id   int         the week-long hypothesis cluster it belongs to;
                           0 = published before clusters existed
  kind         "cluster" | "extra"
                           extra = made with `run --posts N` outside the
                           cluster's seven; kept OUT of the cluster's
                           comparison but tied to it, so its influence on
                           that week can be seen
  experiment   bool | None the ~20% of a cluster that deliberately differs
                           from the cluster's hypothesis; None for extras
                           and for cluster 0
  status       draft → approved → scheduled → published (or rejected / failed;
                           deleted = went out, then removed by hand — keeps its number)

Append-only with last-write-wins, like draft_queue.py and tracking_review.py.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from .config import settings

POSTS_PATH = settings.data_root / "plays" / "_posts.jsonl"

STATUSES = ("draft", "approved", "scheduled", "published", "rejected", "failed", "deleted")


_last_seq = 0


def _seq() -> int:
    """Strictly increasing within a process, and across processes in practice."""
    global _last_seq
    _last_seq = max(time.time_ns(), _last_seq + 1)
    return _last_seq


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_post_id() -> str:
    return str(uuid.uuid4())


def _append(row: dict[str, Any]) -> dict[str, Any]:
    POSTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # `seq` orders rows written within the same second (register + update
    # land together); `ts` alone tied and the SQL could keep the older row.
    row = {**row, "ts": _now_iso(), "seq": _seq()}
    with POSTS_PATH.open("a") as fh:
        fh.write(json.dumps(row, default=str) + "\n")
    return row


def all_posts() -> dict[str, dict[str, Any]]:
    """post_id -> current row."""
    if not POSTS_PATH.exists():
        return {}
    latest: dict[str, dict[str, Any]] = {}
    with POSTS_PATH.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                row = json.loads(line)
                latest[row["post_id"]] = row
    return latest


def get(post_id: str) -> dict[str, Any] | None:
    return all_posts().get(post_id)


def register(post_id: str, product: str, channel: str, *, cluster_id: int, kind: str,
             experiment: bool | None, scheduled_for: str | None = None,
             status: str = "draft") -> dict[str, Any]:
    """A new post enters the table — as a draft, or (backfill) already
    published. `scheduled_for` is the date the cluster design assigned it
    (YYYY-MM-DD), not yet a time."""
    if kind not in ("cluster", "extra"):
        raise ValueError(f"kind must be 'cluster' or 'extra', not {kind!r}")
    if kind == "extra" and experiment is not None:
        raise ValueError("an extra post is outside the experiment; experiment must be None")
    return _append({
        "post_id": post_id, "product": product, "channel": channel,
        "natural_id": None, "cluster_id": cluster_id, "kind": kind,
        "experiment": experiment, "status": status,
        "scheduled_for": scheduled_for, "scheduled_at": None, "published_at": None,
        "post_ref": None,
    })


def update(post_id: str, **fields: Any) -> dict[str, Any]:
    row = get(post_id)
    if row is None:
        raise KeyError(f"{post_id} is not in the post table")
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"unknown status {fields['status']!r}")
    return _append({**row, **fields})


def next_natural_id(product: str, channel: str) -> int:
    used = [r["natural_id"] for r in all_posts().values()
            if r["product"] == product and r["channel"] == channel and r.get("natural_id")]
    return max(used, default=0) + 1


def approve(post_id: str) -> dict[str, Any]:
    """Approval is when a post gets its natural_id. Approving twice keeps
    the first number."""
    row = get(post_id)
    if row is None:
        raise KeyError(f"{post_id} is not in the post table")
    nid = row.get("natural_id") or next_natural_id(row["product"], row["channel"])
    return update(post_id, status="approved", natural_id=nid)


def in_cluster(product: str, channel: str, cluster_id: int,
               kind: str | None = None) -> list[dict[str, Any]]:
    rows = [r for r in all_posts().values()
            if r["product"] == product and r["channel"] == channel
            and r.get("cluster_id") == cluster_id and (kind is None or r["kind"] == kind)]
    return sorted(rows, key=lambda r: (r.get("scheduled_for") or "", r.get("natural_id") or 0))


def backfill_from_tracking() -> int:
    """Give every post published before this table existed a row:
    cluster 0, natural_id in publish order. Idempotent — posts already in
    the table are skipped. A post that never reached the platform (no
    post_ref) gets no number; one later deleted by hand did go out, so it
    keeps its place in the sequence.

    Publish order is each post's FIRST tracking row: later rows are
    checkpoint updates that re-stamp `ts`.
    """
    from . import tracking_review
    from .clusters import ensure_cluster_zero

    path = tracking_review.TRACKING_PATH
    if not path.exists():
        return 0
    first: dict[str, dict[str, Any]] = {}
    latest: dict[str, dict[str, Any]] = {}
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                r = json.loads(line)
                first.setdefault(r["post_id"], r)
                latest[r["post_id"]] = r
    known = all_posts()
    added = 0
    for pid, r in sorted(first.items(), key=lambda kv: kv[1].get("ts", "")):
        cur = latest[pid]
        if pid in known or not cur.get("post_ref"):
            continue
        ensure_cluster_zero(cur["product"], cur["channel"])
        register(pid, cur["product"], cur["channel"], cluster_id=0, kind="cluster",
                 experiment=None, status="published")
        update(pid, natural_id=next_natural_id(cur["product"], cur["channel"]),
               published_at=r.get("ts"), post_ref=cur.get("post_ref"),
               **({"status": "deleted", "deleted_at": cur["deleted_at"]} if cur.get("deleted_at") else {}))
        added += 1
    return added
