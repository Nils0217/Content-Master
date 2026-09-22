"""Audit log (white paper §5.2) — one JSON line per pipeline event.
Basic now; shaped so it can grow into a real governance/compliance report.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .config import settings

LOG_PATH = settings.data_root / "audit" / "events.jsonl"


def log_event(stage: str, event: str, **fields: Any) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "event": event,
        **fields,
    }
    with LOG_PATH.open("a") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
    print(f"[audit] {stage}/{event}: {fields}")
