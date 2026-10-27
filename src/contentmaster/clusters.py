"""Hypothesis clusters: one calendar week, one hypothesis, seven posts.
plays/_clusters.jsonl, one row per cluster state change (latest wins).

2026-10-06, designed with the user. Testing a new idea on every single post
meant each idea was tried once, and one post with one like cannot tell an
idea from luck. A cluster holds ONE hypothesis for a week:

  Monday – Sunday, one post a day        7 posts
  experiment = False                     5 posts follow the hypothesis
  experiment = True                      2 posts (20%, rounded up) deliberately
                                         differ — the comparison is inside the
                                         week, same audience, same conditions

Why a calendar week: the first post's 7-day reading arrives the Monday the
cluster ends, so the next cluster's hypothesis can be decided that day
without a gap. Every weekday appears once per cluster, so day-of-week
effects fall on every cluster alike.

Month boundaries: a cluster is never split. When a month view is needed, a
cluster belongs to the month its Monday falls in (`month_of`). The unit of
analysis is the cluster, never the calendar month.

Numbering is per product × channel; cluster 0 holds everything published
before clusters existed and tests nothing.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .config import settings
from .scoring import control_arm_size

CLUSTERS_PATH = settings.data_root / "plays" / "_clusters.jsonl"

POSTS_PER_CLUSTER = 7
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def experiment_weekdays(cluster_id: int) -> tuple[int, ...]:
    """Which weekdays (0 = Monday) carry the experiment posts.

    Rotates by cluster number, not by calendar, so months with four or
    five weeks make no difference:
        cluster 1 → Mon, Thu    cluster 2 → Tue, Fri    cluster 3 → Wed, Sat
        cluster 4 → Thu, Sun    …    cluster 7 → Sun, Wed
    Over any seven consecutive clusters every weekday is an experiment day
    exactly twice, so no weekday is always on the same side of the
    comparison.
    """
    if cluster_id < 1:
        return ()
    n = control_arm_size(POSTS_PER_CLUSTER)
    first = (cluster_id - 1) % 7
    step = 7 // n if n else 0
    return tuple(sorted((first + i * step) % 7 for i in range(n)))


def week_start_after(today: date) -> date:
    """The Monday a new cluster starts on: the next Monday strictly after
    today, so a cluster is never started part-way through a week."""
    return today + timedelta(days=(7 - today.weekday()) or 7)


def month_of(cluster: dict[str, Any]) -> str:
    """YYYY-MM of the cluster's Monday — the only month it belongs to."""
    return str(cluster["week_start"])[:7]


def slots(cluster_id: int, week_start: date) -> list[dict[str, Any]]:
    """The seven days of a cluster with each one's role."""
    exp = set(experiment_weekdays(cluster_id))
    return [{"date": (week_start + timedelta(days=i)).isoformat(), "weekday": WEEKDAYS[i],
             "experiment": i in exp}
            for i in range(POSTS_PER_CLUSTER)]


_last_seq = 0


def _seq() -> int:
    """Strictly increasing within a process, and across processes in practice."""
    global _last_seq
    _last_seq = max(time.time_ns(), _last_seq + 1)
    return _last_seq


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def all_clusters() -> dict[tuple[str, str, int], dict[str, Any]]:
    if not CLUSTERS_PATH.exists():
        return {}
    latest: dict[tuple[str, str, int], dict[str, Any]] = {}
    with CLUSTERS_PATH.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                row = json.loads(line)
                latest[(row["product"], row["channel"], row["cluster_id"])] = row
    return latest


def for_product(product: str, channel: str) -> list[dict[str, Any]]:
    return sorted((c for (p, ch, _), c in all_clusters().items() if p == product and ch == channel),
                  key=lambda c: c["cluster_id"])


def get(product: str, channel: str, cluster_id: int) -> dict[str, Any] | None:
    return all_clusters().get((product, channel, cluster_id))


def _append(row: dict[str, Any]) -> dict[str, Any]:
    CLUSTERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # `seq` orders rows written within the same second (register + update
    # land together); `ts` alone tied and the SQL could keep the older row.
    row = {**row, "ts": _now_iso(), "seq": _seq()}
    with CLUSTERS_PATH.open("a") as fh:
        fh.write(json.dumps(row, default=str) + "\n")
    return row


def create(product: str, channel: str, week_start: date, hypothesis: dict[str, Any] | None,
           learned_from: int | None) -> dict[str, Any]:
    """Open the next cluster. `learned_from` is the cluster whose 7-day
    results chose this hypothesis; None when it was planned without one
    (the first cluster, or several weeks generated at once)."""
    if week_start.weekday() != 0:
        raise ValueError(f"a cluster starts on a Monday, not {week_start:%a %Y-%m-%d}")
    existing = for_product(product, channel)
    if any(str(c["week_start"]) == week_start.isoformat() for c in existing
           if c["cluster_id"] > 0 and c.get("status") != "cancelled"):
        raise ValueError(f"a cluster already covers the week of {week_start}")
    cluster_id = max((c["cluster_id"] for c in existing), default=0) + 1
    return _append({
        "product": product, "channel": channel, "cluster_id": cluster_id,
        "week_start": week_start.isoformat(),
        "experiment_weekdays": [WEEKDAYS[d] for d in experiment_weekdays(cluster_id)],
        "hypothesis": hypothesis, "hypothesis_id": (hypothesis or {}).get("id"),
        "learned_from": learned_from, "post_time": None, "status": "drafting",
        "result": None,
    })


def update(product: str, channel: str, cluster_id: int, **fields: Any) -> dict[str, Any]:
    row = get(product, channel, cluster_id)
    if row is None:
        raise KeyError(f"no cluster {cluster_id} for {product} / {channel}")
    return _append({**row, **fields})


def ensure_cluster_zero(product: str, channel: str) -> dict[str, Any]:
    """Cluster 0: everything published before clusters existed."""
    existing = get(product, channel, 0)
    if existing:
        return existing
    return _append({
        "product": product, "channel": channel, "cluster_id": 0, "week_start": None,
        "experiment_weekdays": [], "hypothesis": None, "hypothesis_id": None,
        "learned_from": None, "post_time": None, "status": "legacy",
        "result": None,
        "note": "published before the cluster design (2026-10-06); tests nothing",
    })


def cluster_line(entry: dict[str, Any]) -> str:
    """"Cluster 1 · Wed 2026-10-28 · Main — makes this week's change".
    The one wording for a cluster post's place in the week: browser review,
    terminal review and the end-of-run list all print this."""
    day = entry.get("scheduled_for") or ""
    try:
        day = f"{date.fromisoformat(day):%a} {day}"
    except ValueError:
        pass
    role = ("Experiment — must NOT make this week's change" if entry.get("experiment")
            else "Main — makes this week's change")
    return f"Cluster {entry.get('cluster_id')} · {day} · {role}"


# Statuses that occupy a day: a post on its way out, or out.
LIVE = ("draft", "approved", "scheduled", "published")


def open_slots(c: dict[str, Any], today: date) -> list[dict[str, Any]]:
    """Days of this cluster from today on that have no live post. Past days
    are not refilled — a post cannot go out yesterday."""
    from . import posts

    filled = {r.get("scheduled_for") for r in posts.in_cluster(c["product"], c["channel"],
                                                              c["cluster_id"], kind="cluster")
              if r["status"] in LIVE}
    return [sl for sl in slots(c["cluster_id"], date.fromisoformat(c["week_start"]))
            if sl["date"] >= today.isoformat() and sl["date"] not in filled]


def running(product: str, channel: str, today: date) -> list[dict[str, Any]]:
    """Clusters whose week has not ended (this week's and any later one),
    earliest first. Cancelled ones are not running."""
    return [c for c in for_product(product, channel)
            if c["cluster_id"] > 0 and c.get("week_start") and c.get("status") != "cancelled"
            and date.fromisoformat(c["week_start"]) + timedelta(days=7) > today]


def target(product: str, channel: str, today: date) -> tuple[dict[str, Any] | None, date | None, str]:
    """What `run --cluster` should do today: (cluster to fill, None, why) or
    (None, Monday to open a new one on, why) or (None, None, why not).

    2026-10-26: this was "always open next Monday's cluster". Run on cluster
    1's own Monday, that opened cluster 2 a week early — cluster 1's empty
    weekend never filled, and cluster 2 took a hypothesis its predecessor's
    results had not chosen yet. Now: the earliest running cluster with an
    open day is filled first; a new one opens only when none is running —
    today if today is a Monday, otherwise next Monday.
    """
    live = running(product, channel, today)
    for c in live:
        if open_slots(c, today):
            return c, None, f"cluster {c['cluster_id']} (week of {c['week_start']}) has open days"
    if live:
        last = live[-1]
        ends = date.fromisoformat(last["week_start"]) + timedelta(days=7)
        return None, None, (f"cluster {last['cluster_id']} (week of {last['week_start']}) is full and still "
                            f"running. The next cluster opens on {ends} — its hypothesis comes from this "
                            "week's results.")
    week = today if today.weekday() == 0 else week_start_after(today)
    return None, week, f"no cluster is running — opening the week of {week}"


def cancel(product: str, channel: str, cluster_id: int, reason: str) -> list[str]:
    """Withdraw a cluster that should not run: its unpublished posts become
    `cancelled` (natural_ids are kept — numbers are never reused), and the
    week is free again. Published posts are left as they are."""
    from . import draft_queue, posts

    done = []
    for r in posts.in_cluster(product, channel, cluster_id):
        if r["status"] in ("draft", "approved", "scheduled"):
            posts.update(r["post_id"], status="cancelled")
            if r["post_id"] in draft_queue._latest_entries():
                draft_queue.update_entry(r["post_id"], status="cancelled")
            done.append(r["post_id"])
    update(product, channel, cluster_id, status="cancelled", cancel_reason=reason)
    return done

