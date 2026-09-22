"""Local metrics store — replaces hotdata_client.py's role (see
docs/SCHEDULE.md Phase 0, docs/LOG.md 2026-09-13: hotdata.dev retired).

Writes/reads plain JSONL — the same pattern plays/_failures.jsonl and
audit/events.jsonl already use. warehouse/'s dbt project reads it straight
off disk via DuckDB's native JSON support (see
warehouse/models/staging/stg_post_metrics.sql) — no live DuckDB
connection, no CLI, no account needed here.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .config import settings

METRICS_PATH = settings.data_root / "metrics" / "post_metrics.jsonl"


def write_metrics(post_id: str, channel: str, metrics: dict[str, Any]) -> None:
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "post_id": post_id,
        "channel": channel,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **metrics,
    }
    with METRICS_PATH.open("a") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def get_metrics(post_id: str) -> dict[str, Any] | None:
    """Returns the most recently written record for post_id, or None if
    nothing's been recorded for it yet.
    """
    if not METRICS_PATH.exists():
        return None
    latest = None
    with METRICS_PATH.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("post_id") == post_id:
                latest = record
    return latest

# 2026-09-21: mock_metrics() deleted. It returned invented engagement
# numbers whenever a real reading could not be taken — no platform
# adapter, a simulated publish, or a failed metrics call — and wrote them
# to metrics/post_metrics.jsonl in exactly the shape a real reading has.
# Every "fabricated data reached the ledger" incident traced back to it:
# a post deleted by hand got random likes, and a post that was never
# published at all would have too. There is no longer any path that
# invents a number. If a real reading cannot be taken, nothing is
# written and the checkpoint stays due, so the next run retries it (see
# pipeline._pull_checkpoint_metrics).
