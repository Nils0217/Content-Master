"""Which errors keep happening — computed from the audit log, on demand.

2026-09-21, /grill-me design session. The point of this is NOT to help the
pipeline recover from recurring errors. It is to make recurring errors
visible so they get *eliminated*.

That distinction was the whole argument. A system that recovers
gracefully from an error it has seen before never generates enough
pressure to fix the underlying cause — the error is caught every time, so
it is never quite painful enough. The 322-character post is the example:
the limit was knowable in advance the entire time, and the right response
was to enforce it before generation, not to get better at handling the
rejection. This index exists to produce the list of "errors we keep
handling that we should be preventing instead".

Derived, never written. `audit/events.jsonl` stays the single append-only
write point; anything else is a second source of truth that will drift
from it (see docs/SCHEDULE.md Phase 10, which records that exact failure
happening twice with duplicated constants). At a few hundred lines the
recomputation is free, and it can never be stale.

Retrieval on top of this — feeding a past analysis to the LLM so it can
propose a fix — is deliberately NOT here. It is scheduled as an era 1
requirement; see docs/SCHEDULE.md.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterator

from .config import settings

AUDIT_PATH = settings.data_root / "audit" / "events.jsonl"

# Anything variable in a message, replaced so two occurrences of the same
# problem collapse to one signature. Without this, "got 322" and "got 305"
# are two different errors and the counter never learns anything.
_NORMALIZERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"at://[^\s'\"]+"), "<uri>"),
    (re.compile(r"\bdid:[a-z]+:[A-Za-z0-9]+"), "<did>"),
    (re.compile(r"\b[0-9a-f]{8,}\b"), "<hash>"),
    (re.compile(r"(?<![\w/])/[^\s'\":]+"), "<path>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ][\d:.+-]+"), "<timestamp>"),
    (re.compile(r"\b\d+\b"), "<n>"),
    (re.compile(r"\s+"), " "),
)


def normalize(message: str) -> str:
    text = (message or "").strip()
    for pattern, replacement in _NORMALIZERS:
        text = pattern.sub(replacement, text)
    return text[:200]


def signature(stage: str, error_type: str, message: str) -> str:
    """stage + exception type + normalized message.

    Stage is almost free and separates failures that share an exception
    class but not a cause — a PlatformAPIError while publishing and one
    while reading metrics are different problems with different fixes.
    """
    return f"{stage or '?'} | {error_type or '?'} | {normalize(message)}"


def _error_events() -> Iterator[dict[str, Any]]:
    if not AUDIT_PATH.exists():
        return
    with AUDIT_PATH.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("event") in ("failed", "blocked", "unavailable", "unmeasurable",
                                       "post.deleted", "not_tracked"):
                yield record


def error_counts() -> dict[str, dict[str, Any]]:
    """Every error signature seen, with how often and when."""
    out: dict[str, dict[str, Any]] = {}
    for record in _error_events():
        message = record.get("reason") or "; ".join(record.get("reasons") or []) or record.get("event", "")
        sig = signature(record.get("stage", ""), record.get("error_type", record.get("event", "")), message)
        entry = out.setdefault(sig, {"count": 0, "first_seen": record.get("ts", ""),
                                      "last_seen": "", "kind": record.get("kind", "")})
        entry["count"] += 1
        entry["last_seen"] = record.get("ts", entry["last_seen"])
    return out


def recurring_errors(min_count: int = 2) -> list[tuple[str, dict[str, Any]]]:
    """The ones worth preventing rather than handling, most frequent first.

    `min_count` defaults to 2 because the second occurrence is the signal:
    the first time is an incident, the second is a pattern.
    """
    counts = error_counts()
    return sorted(
        ((sig, info) for sig, info in counts.items() if info["count"] >= min_count),
        key=lambda kv: kv[1]["count"], reverse=True,
    )
