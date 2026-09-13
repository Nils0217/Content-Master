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
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings

METRICS_PATH = settings.project_root / "metrics" / "post_metrics.jsonl"


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


def mock_metrics(post_id: str) -> dict[str, Any]:
    """Clearly-labeled stand-in for live engagement data, used when there's
    no real published post to pull metrics from (channel has no
    implemented platform adapter, or the publish fell back to simulated).
    """
    impressions = random.randint(400, 4000)
    clicks = int(impressions * random.uniform(0.01, 0.06))
    conversions = int(clicks * random.uniform(0.02, 0.15))
    return {
        "source": "MOCK — no live post to pull metrics from",
        "impressions": impressions,
        "clicks": clicks,
        "conversions": conversions,
        "ctr": round(clicks / impressions, 4) if impressions else 0,
    }
