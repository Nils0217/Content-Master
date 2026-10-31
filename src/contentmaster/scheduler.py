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

import fcntl
import os
import plistlib
import subprocess
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from . import audit, posts, warehouse
from .config import settings

AGENT_LABEL = "com.contentmaster.publish"
AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"
LOCK_PATH = settings.data_root / "plays" / ".publish.lock"

REVIEW_AGENT_LABEL = "com.contentmaster.review"
REVIEW_AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / f"{REVIEW_AGENT_LABEL}.plist"
REVIEW_LOCK_PATH = settings.data_root / "plays" / ".review.lock"
# After the usual 18:00 post, so publishing and analysing do not overlap.
REVIEW_DEFAULT_TIME = "18:30"
REVIEW_TIERS = ("24h", "7d", "30d")
# Each analysis is three model calls on an 8GB machine.
REVIEW_WAKE_LIMIT = 3

_held_locks: dict[Path, Any] = {}

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
    if c.get("status") == "cancelled":
        # 2026-10-26: pressing Schedule on a cancelled cluster sent nothing
        # but flipped its status back to "scheduled", rewriting the record.
        raise ValueError(f"cluster {cluster_id} was cancelled — it cannot be scheduled")
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


def needs_scheduling(c: dict[str, Any]) -> list[dict[str, Any]]:
    """Approved posts in this cluster that have no time yet."""
    return [r for r in posts.in_cluster(c["product"], c["channel"], c["cluster_id"], kind="cluster")
            if r["status"] == "approved"]


def default_cluster(candidates: list[dict[str, Any]], today: date | None = None) -> int:
    """Index of the cluster the Schedule tab and `contentmaster schedule`
    should start on: the earliest running cluster with approved posts
    waiting for a time, else the earliest running one, else the newest.

    2026-10-26: both defaulted to the newest cluster, so "Schedule post"
    scheduled a cluster that should not have existed yet while the current
    week's five approved posts never got a time — Monday's post missed 18:00.
    """
    today = today or date.today()
    run = [i for i, c in enumerate(candidates)
           if c.get("status") != "cancelled" and c.get("week_start")
           and date.fromisoformat(c["week_start"]).toordinal() + 7 > today.toordinal()]
    run.sort(key=lambda i: candidates[i]["week_start"])
    for i in run:
        if needs_scheduling(candidates[i]):
            return i
    return run[0] if run else len(candidates) - 1


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
    scheduled for the next wake; any other marks it failed. A send that may
    have gone through is checked against the account before it is retried.
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
            # A human approved this post with this file, and nobody is watching
            # now: going out without it would not be the post they approved.
            media = e.get("video_path") or e.get("image_path")
            if media and not Path(media).exists():
                reason = f"approved media not found: {media}"
                posts.update(r["post_id"], status="failed")
                draft_queue.update_entry(r["post_id"], status="publish_failed",
                                         failure_kind="media_missing", failure_reason=reason)
                audit.log_event("scheduler", "media_missing", post_id=r["post_id"],
                                 natural_id=r.get("natural_id"), path=media)
                print(f"[publish-due] #{r.get('natural_id')} not published — {reason} — marked failed.")
                continue
            image = video = None
            if e.get("video_path"):
                video = Path(media).read_bytes()
            elif media:
                image = Path(media).read_bytes()
            draft ={"id": r["post_id"], "channel": e["channel"], "topic": e.get("topic", ""),
                     "brief": e.get("brief", ""), **{k: e.get(k) for k in LABEL_KEYS if e.get(k) is not None}}
            # Marked before sending, so a run killed mid-send is treated like a
            # lost connection: the account is checked before anything is resent.
            was_uncertain = bool(e.get("send_uncertain"))
            draft_queue.update_entry(r["post_id"], send_uncertain=True)
            try:
                published = step5_publish(draft, e["text"], image=image,
                                          image_alt=(e.get("image_description") or e.get("brief") or "")[:200],
                                          video=video, video_alt=e.get("video_alt", ""),
                                          original_text=e.get("original_text") or e["text"],
                                          acknowledged=True, check_first=was_uncertain)
            except PublishFailed as err:
                keep = err.failure.retry_unchanged
                draft_queue.update_entry(r["post_id"], send_uncertain=err.failure.uncertain
                                         or (was_uncertain and keep))
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
            draft_queue.update_entry(r["post_id"], status="published", send_uncertain=False)
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


def review_due(limit: int = REVIEW_WAKE_LIMIT) -> int:
    """Analyse what has come due, for a human to decide later. What launchd
    runs for `contentmaster review --schedule`.

    Stops at the queue on purpose (2026-10-30): the analysis is made
    unattended, but only a human's Confirm or Disagree finishes it, and
    no browser opens while nobody asked for one. `limit` keeps one wake from
    holding Ollama for the whole evening: a backlog drains over several days,
    newest tier first. Returns how many analyses were queued.
    """
    from . import analysis_queue, discuss
    from .pipeline import _due_unqueued, _queue_due

    if not _take_lock(REVIEW_LOCK_PATH):
        print("[review-due] Another review run is in progress — leaving it to that one.")
        return 0
    try:
        if not any(_due_unqueued(c) for c in REVIEW_TIERS):
            print("[review-due] Nothing new to analyse.")
            return 0
        if not discuss.model_server_up():
            msg = "Scheduled review could not run: Ollama is not reachable. It will try again at the next wake."
            print(f"[review-due] {msg}")
            audit.log_event("scheduler", "failed", kind="review_due", reason="Ollama not reachable")
            notify(msg)
            return 0
        n = 0
        try:
            for checkpoint in REVIEW_TIERS:
                n += _queue_due(checkpoint, limit=limit - n)[0]
        except warehouse.WarehouseFailed as exc:
            msg = "Scheduled review stopped: post history could not be updated. It will try again at the next wake."
            print(f"[review-due] {msg} {exc}")
            notify(msg)
            return n
        waiting = len(analysis_queue.find_pending())
        audit.log_event("scheduler", "review_due", queued=n, waiting=waiting)
        print(f"[review-due] {n} analysis(es) queued; {waiting} waiting for your decision.")
        if n:
            notify(f"{waiting} analysis(es) are waiting for your decision. Run: contentmaster review")
        return n
    finally:
        _release_lock(REVIEW_LOCK_PATH)


def notify(message: str) -> bool:
    """A macOS notification. Never from an isolated data root."""
    if settings.is_isolated_data_root:
        return False
    safe = message.replace("\\", "\\\\").replace('"', '\\"')
    done = subprocess.run(
        [os.environ.get("CONTENTMASTER_NOTIFY", "/usr/bin/osascript"), "-e",
         f'display notification "{safe}" with title "ContentMaster"'],
        capture_output=True, check=False)
    return done.returncode == 0


def _take_lock(path: Path | None = None) -> bool:
    """An operating-system lock, held until _release_lock or until the process
    ends, however it ends. 2026-10-30 (code review): this was "does the file
    exist, then write it", and callers released together could all win."""
    path = path or LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    _held_locks[path] = fh
    return True


def _release_lock(path: Path | None = None) -> None:
    fh = _held_locks.pop(path or LOCK_PATH, None)
    if fh:
        fh.close()


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
    if not times:
        _unload_agent(AGENT_PATH)
        AGENT_PATH.unlink(missing_ok=True)
        audit.log_event("scheduler", "agent.removed")
        return "removed (nothing scheduled)"
    error = _load_agent(AGENT_PATH, agent_plist(times), times=times)
    return f"failed to load: {error}" if error else f"wakes daily at {', '.join(times)}"


def _unload_agent(path: Path) -> None:
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)],
                   capture_output=True, check=False)


def _load_agent(path: Path, plist: dict[str, Any], **log: Any) -> str:
    """Replace the agent at `path`. Returns launchd's error, empty on success."""
    _unload_agent(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    (settings.project_root / "logs").mkdir(exist_ok=True)
    with path.open("wb") as fh:
        plistlib.dump(plist, fh)
    done = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)],
                          capture_output=True, text=True, check=False)
    audit.log_event("scheduler", "agent.installed", agent=plist["Label"], returncode=done.returncode,
                     stderr=done.stderr.strip()[:300], **log)
    return done.stderr.strip() if done.returncode != 0 else ""


def review_agent_plist(hhmm: str) -> dict[str, Any]:
    logs = settings.project_root / "logs"
    return {
        "Label": REVIEW_AGENT_LABEL,
        "ProgramArguments": ["/bin/sh", str(settings.project_root / "scripts" / "review_due.sh")],
        "WorkingDirectory": str(settings.project_root),
        "StartCalendarInterval": [{"Hour": int(hhmm[:2]), "Minute": int(hhmm[3:])}],
        "StandardOutPath": str(logs / "review.log"),
        "StandardErrorPath": str(logs / "review.log"),
        "RunAtLoad": False,
    }


def schedule_review(hhmm: str = REVIEW_DEFAULT_TIME) -> str:
    """`contentmaster review --schedule`: analyse what is due once a day.
    Same rule as sync_agent: an isolated data root never touches the Mac."""
    if hhmm not in TIME_CHOICES:
        raise ValueError(f"{hhmm!r} is not one of the half-hour times between 06:00 and 23:30")
    if settings.is_isolated_data_root:
        return "skipped (isolated data root)"
    error = _load_agent(REVIEW_AGENT_PATH, review_agent_plist(hhmm), time=hhmm)
    return f"failed to load: {error}" if error else f"reviews daily at {hhmm}"


def unschedule_review() -> str:
    if settings.is_isolated_data_root:
        return "skipped (isolated data root)"
    _unload_agent(REVIEW_AGENT_PATH)
    REVIEW_AGENT_PATH.unlink(missing_ok=True)
    audit.log_event("scheduler", "agent.removed", agent=REVIEW_AGENT_LABEL)
    return "automatic review is off"


def review_schedule() -> str | None:
    """The daily review time, or None when it is off. The agent file is the
    record, so this cannot disagree with what the Mac will actually do."""
    if settings.is_isolated_data_root or not REVIEW_AGENT_PATH.exists():
        return None
    with REVIEW_AGENT_PATH.open("rb") as fh:
        at = plistlib.load(fh)["StartCalendarInterval"][0]
    return f"{at['Hour']:02d}:{at['Minute']:02d}"
