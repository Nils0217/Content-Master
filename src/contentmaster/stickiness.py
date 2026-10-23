"""The two numbers every hypothesis is read on (2026-10-22, decided with the
user): follower stickiness and follower change.

Before this, each hypothesis named its own WATCH metric (likes, reposts…),
and advisors chose whatever sounded plausible. The user's goal for this
account is an audience that comes back, and before 50 followers, getting
followers at all — so the system watches the same two things every time
and no model chooses.

  stickiness   per post: of the accounts that liked, reposted or replied,
               the share who had engaged with an EARLIER post. Bookmarks
               are private on Bluesky, so bookmarkers are not counted.
               None = nobody engaged (undefined, not 0); 0.0 = people
               engaged and none were returning.
  follower     per cluster week only: followers are an account-level number
  change       that also moves for reasons no post caused (unfollows, bot
               accounts), so it is NOT split across individual posts — it
               compares clusters with each other.

Account snapshots (plays/_account.jsonl) are written at every publish and
every `contentmaster refresh`.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .config import settings

ACCOUNT_PATH = settings.data_root / "plays" / "_account.jsonl"
ENGAGER_KINDS = ("likers", "reposters", "repliers")


# --- follower change ---------------------------------------------------------

def record_account_snapshot(channel: str, snapshot: dict[str, Any], source: str) -> None:
    if not snapshot or snapshot.get("followers") is None:
        return
    ACCOUNT_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "channel": channel,
           "source": source, **{k: snapshot.get(k) for k in ("followers", "following", "posts")}}
    with ACCOUNT_PATH.open("a") as fh:
        fh.write(json.dumps(row) + "\n")


def _snapshots(channel: str) -> list[dict[str, Any]]:
    if not ACCOUNT_PATH.exists():
        return []
    rows = [json.loads(l) for l in ACCOUNT_PATH.read_text().splitlines() if l.strip()]
    return sorted((r for r in rows if r.get("channel") == channel), key=lambda r: r["ts"])


def follower_change(channel: str, start_iso: str, end_iso: str) -> dict[str, Any]:
    """Net followers gained between two moments, from the snapshots nearest
    inside the window. {"change", "from", "to", "from_ts", "to_ts"}, with
    change None when fewer than two snapshots fall inside — said, not
    guessed."""
    inside = [r for r in _snapshots(channel) if start_iso <= r["ts"] <= end_iso]
    if len(inside) < 2:
        return {"change": None, "from": None, "to": None, "from_ts": None, "to_ts": None,
                "snapshots": len(inside)}
    a, b = inside[0], inside[-1]
    return {"change": b["followers"] - a["followers"], "from": a["followers"], "to": b["followers"],
            "from_ts": a["ts"], "to_ts": b["ts"], "snapshots": len(inside)}


# --- stickiness --------------------------------------------------------------

def engagers_of(post_id: str) -> set[str] | None:
    """Everyone who liked, reposted or replied, from the latest reading that
    could ask. None when no reading has engager data."""
    from . import metrics_store

    path = metrics_store.METRICS_PATH
    if not path.exists():
        return None
    found = None
    for line in path.read_text().splitlines():
        if post_id not in line:
            continue
        r = json.loads(line)
        if r.get("post_id") == post_id and isinstance(r.get("engagers"), dict):
            found = r["engagers"]
    if found is None:
        return None
    return {a for kind in ENGAGER_KINDS for a in (found.get(kind) or [])}


def stickiness(post_id: str, product: str, channel: str) -> dict[str, Any]:
    """{"value", "engagers", "returning", "known"} for one post.

    Earlier = posts in the post table published before this one. A prior
    post with no engager data is skipped and counted in `unknown_prior`, so
    a low value can be read against how much history was missing.
    """
    from . import posts

    mine = engagers_of(post_id)
    if mine is None:
        return {"value": None, "engagers": None, "returning": None, "known": False}
    table = posts.all_posts()
    me = table.get(post_id) or {}
    before = [r for r in table.values()
              if r["product"] == product and r["channel"] == channel and r["post_id"] != post_id
              and r.get("published_at") and (not me.get("published_at")
                                             or r["published_at"] < me["published_at"])]
    prior: set[str] = set()
    unknown = 0
    for r in before:
        e = engagers_of(r["post_id"])
        if e is None:
            unknown += 1
        else:
            prior |= e
    returning = mine & prior
    return {"value": (len(returning) / len(mine)) if mine else None,
            "engagers": len(mine), "returning": len(returning), "known": True,
            "unknown_prior": unknown}
