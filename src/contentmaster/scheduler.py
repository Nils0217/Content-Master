"""Scheduled publishing for cluster posts (2026-10-25, stage 3 of the cluster
design, agreed with the user).

The user's rule since the start: an LLM never publishes on its own, "unless
we build an auto-publish button". This is that button, and it is pressed by
a human after approving each post:

  approve each post (approve_for_schedule)  →  choose one time for the
  cluster, press "Schedule post"  →  each approved post gets its own day at
  that time  →  at that time macOS wakes `contentmaster publish-due`

One time per cluster on purpose: the hour is a condition of the experiment,
so all seven posts share it.

Waking: Bluesky has no scheduling API, so this Mac does it. The user asked
for the Mac to run only at the posting moment, not to poll every few
minutes — a launchd agent with one StartCalendarInterval per distinct
posting time. launchd runs a missed interval when the Mac wakes; anything
overdue is published then, with how late it went recorded on the post.
Nothing here installs or touches launchd from an isolated data root, so
tests never change the real Mac.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from . import audit, posts
from .config import settings

AGENT_LABEL = "com.contentmaster.publish"
AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"
LOCK_PATH = settings.data_root / "plays" / ".publish.lock"

# Half-hour steps across the waking day — the dropdown and the CLI accept these.
TIME_CHOICES = [f"{h:02d}:{m:02d}" for h in range(6, 24) for m in (0, 30)]


def _local(day: str, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(date.fromisoformat(day), time(h, m)).astimezone()


def schedule_cluster(product: str, channel: str, cluster_id: int, hhmm: str) -> dict[str, Any]:
    """Give every approved post in the cluster its day at `hhmm` (local time).
    Returns {"scheduled": [...], "not_approved": [...], "past": [...]}.
    Posts approved later are scheduled at approval (see schedule_if_cluster_timed)."""
    from . import clusters

    if hhmm not in TIME_CHOICES:
        raise ValueError(f"{hhmm!r} is not one of the half-hour times between 06:00 and 23:30")
    c = clusters.get(product, channel, cluster_id)
    if not c or cluster_id < 1:
        raise KeyError(f"no cluster {cluster_id} for {product} / {channel}")
    out: dict[str, list[str]] = {"scheduled": [], "not_approved": [], "past": []}
    now = datetime.now().astimezone()
    live = [r for r in posts.in_cluster(product, channel, cluster_id, kind="cluster")
            if r["status"] in ("draft", "approved", "scheduled")]
    for r in live:
        if r["status"] == "draft":
            out["not_approved"].append(r["scheduled_for"])
            continue
        at = _local(r["scheduled_for"], hhmm)
        posts.update(r["post_id"], status="scheduled", scheduled_at=at.isoformat())
        out["scheduled"].append(r["scheduled_for"])
        if at < now:
            out["past"].append(r["scheduled_for"])
    clusters.update(product, channel, cluster_id, post_time=hhmm, status="scheduled")
    audit.log_event("scheduler", "cluster.scheduled", product=product, channel=channel,
                     cluster_id=cluster_id, time=hhmm, **out)
    sync_agent()
    return out


def schedule_if_cluster_timed(row: dict[str, Any]) -> dict[str, Any]:
    """Called at approval: a post approved after its cluster was given a time
    joins the schedule at that time without another button press."""
    from . import clusters

    c = clusters.get(row["product"], row["channel"], row.get("cluster_id") or 0)
    if not c or not c.get("post_time") or row.get("kind") != "cluster":
        return row
    at = _local(row["scheduled_for"], c["post_time"])
    row = posts.update(row["post_id"], status="scheduled", scheduled_at=at.isoformat())
    sync_agent()
    return row


def due(now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now().astimezone()
    rows = [r for r in posts.all_posts().values()
            if r["status"] == "scheduled" and r.get("scheduled_at")
            and datetime.fromisoformat(r["scheduled_at"]) <= now]
    return sorted(rows, key=lambda r: r["scheduled_at"])


def upcoming() -> list[dict[str, Any]]:
    rows = [r for r in posts.all_posts().values() if r["status"] == "scheduled"]
    return sorted(rows, key=lambda r: r.get("scheduled_at") or "")


def publish_due(now: datetime | None = None) -> int:
    """Publish every scheduled post whose time has come. What launchd runs.

    Goes through the same step5_publish / step6_queue_for_review every
    publish uses. The "does this look like a post" guard already ran at
    approval with a human present (approve_for_schedule), so it is
    acknowledged here; the platform's hard limits still apply. A failure
    the platform may not repeat (network, rate limit) leaves the post
    scheduled for the next wake; any other marks it failed.
    """
    from . import draft_queue
    from .pipeline import LABEL_KEYS, PublishFailed, step5_publish, step6_queue_for_review

    if not _take_lock():
        print("[publish-due] Another publish run is in progress — leaving it to that one.")
        return 0
    try:
        now = now or datetime.now().astimezone()
        queue = draft_queue._latest_entries()
        n = 0
        for r in due(now):
            e = queue.get(r["post_id"])
            if not e:
                posts.update(r["post_id"], status="failed")
                print(f"[publish-due] #{r.get('natural_id')} has no approved text on record — marked failed.")
                continue
            image = video = None
            if e.get("video_path") and Path(e["video_path"]).exists():
                video = Path(e["video_path"]).read_bytes()
            elif e.get("image_path") and Path(e["image_path"]).exists():
                image = Path(e["image_path"]).read_bytes()
            draft = {"id": r["post_id"], "channel": e["channel"], "topic": e.get("topic", ""),
                     "brief": e.get("brief", ""), **{k: e.get(k) for k in LABEL_KEYS if e.get(k) is not None}}
            try:
                published = step5_publish(draft, e["text"], image=image,
                                          image_alt=(e.get("image_description") or e.get("brief") or "")[:200],
                                          video=video, original_text=e.get("original_text") or e["text"],
                                          acknowledged=True)
            except PublishFailed as err:
                keep = err.failure.retry_unchanged
                if not keep:
                    posts.update(r["post_id"], status="failed")
                    draft_queue.update_entry(r["post_id"], status="publish_failed",
                                             failure_kind=err.failure.kind, failure_reason=err.failure.reason)
                print(f"[publish-due] #{r.get('natural_id')} not published ({err.failure.kind}) — "
                      + ("still scheduled, tried again at the next wake." if keep else "marked failed."))
                continue
            step6_queue_for_review(e["product"], e["channel"], published,
                                   edited=bool(e.get("edited")), reviewer_note=e.get("reviewer_note", ""),
                                   labels={k: e.get(k) for k in LABEL_KEYS if e.get(k) is not None})
            draft_queue.update_entry(r["post_id"], status="published")
            late = int((now - datetime.fromisoformat(r["scheduled_at"])).total_seconds() // 60)
            posts.update(r["post_id"], late_minutes=max(late, 0))
            audit.log_event("scheduler", "published", post_id=r["post_id"],
                             natural_id=r.get("natural_id"), late_minutes=max(late, 0))
            print(f"[publish-due] #{r.get('natural_id')} published"
                  + (f" ({late} min late)" if late > 5 else "") + ".")
            n += 1
        return n
    finally:
        _release_lock()


def _take_lock() -> bool:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        try:
            os.kill(int(LOCK_PATH.read_text().strip()), 0)
            return False            # the other run is alive
        except (ValueError, ProcessLookupError, PermissionError):
            pass                    # stale lock from a run that died
    LOCK_PATH.write_text(str(os.getpid()))
    return True


def _release_lock() -> None:
    try:
        if LOCK_PATH.read_text().strip() == str(os.getpid()):
            LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


# --- the launchd agent ---------------------------------------------------------

def agent_plist(times: list[str]) -> dict[str, Any]:
    """The agent for these HH:MM times — one StartCalendarInterval each."""
    # Through a shell wrapper, not the venv directly: if the venv's Python
    # is gone (it was, 2026-10-25), the wrapper raises a notification
    # instead of the post silently not going out.
    wrapper = settings.project_root / "scripts" / "publish_due.sh"
    logs = settings.project_root / "logs"
    return {
        "Label": AGENT_LABEL,
        "ProgramArguments": ["/bin/sh", str(wrapper)],
        "WorkingDirectory": str(settings.project_root),
        "StartCalendarInterval": [{"Hour": int(t[:2]), "Minute": int(t[3:])} for t in sorted(times)],
        "StandardOutPath": str(logs / "publish.log"),
        "StandardErrorPath": str(logs / "publish.log"),
        "RunAtLoad": False,
    }


def sync_agent() -> str:
    """Install, update or remove the agent to match the scheduled posts.
    Never from an isolated data root: tests and checks do not touch the Mac."""
    if settings.is_isolated_data_root:
        return "skipped (isolated data root)"
    times = sorted({datetime.fromisoformat(r["scheduled_at"]).strftime("%H:%M")
                    for r in upcoming() if r.get("scheduled_at")})
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(AGENT_PATH)],
                   capture_output=True, check=False)
    if not times:
        AGENT_PATH.unlink(missing_ok=True)
        audit.log_event("scheduler", "agent.removed")
        return "removed (nothing scheduled)"
    AGENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    (settings.project_root / "logs").mkdir(exist_ok=True)
    with AGENT_PATH.open("wb") as fh:
        plistlib.dump(agent_plist(times), fh)
    done = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(AGENT_PATH)],
                          capture_output=True, text=True, check=False)
    audit.log_event("scheduler", "agent.installed", times=times, returncode=done.returncode,
                     stderr=done.stderr.strip()[:300])
    if done.returncode != 0:
        return f"failed to load: {done.stderr.strip()}"
    return f"wakes daily at {', '.join(times)}"
