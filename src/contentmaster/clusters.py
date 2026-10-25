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
    if any(str(c["week_start"]) == week_start.isoformat() for c in existing if c["cluster_id"] > 0):
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

