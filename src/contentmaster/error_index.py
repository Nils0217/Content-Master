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

Retrieval (2026-10-30, docs/SCHEDULE.md Phase 11): report() adds each
signature's recent occurrences and any matching write-up in
docs/ERROR_LOG.md, and propose_fix() hands those to a model. A proposal is
text a human accepts or rejects; nothing here ever applies one. Proposals
are decisions rather than counts, so they get their own ledger.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import settings

AUDIT_PATH = settings.data_root / "audit" / "events.jsonl"
PROPOSALS_PATH = settings.data_root / "plays" / "_error_proposals.jsonl"
ERROR_LOG_PATH = settings.project_root / "docs" / "ERROR_LOG.md"
FIX_MODEL = os.environ.get("ERROR_FIX_MODEL", os.environ.get("LLM_IMPROVE_MODEL", "llama3.2:3b"))
# Words every write-up shares, which would match anything to anything.
_COMMON_WORDS = {"error", "errors", "failed", "failure", "with", "from", "that", "this", "none"}

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
                                       "post.deleted", "not_tracked", "media_missing", "model_error"):
                yield record


def error_counts() -> dict[str, dict[str, Any]]:
    """Every error signature seen, with how often and when."""
    out: dict[str, dict[str, Any]] = {}
    for record in _error_events():
        message = (record.get("reason") or "; ".join(record.get("reasons") or [])
                   or record.get("error") or record.get("event", ""))
        sig = signature(record.get("stage", ""), record.get("error_type", record.get("event", "")), message)
        entry = out.setdefault(sig, {"count": 0, "first_seen": record.get("ts", ""),
                                      "last_seen": "", "kind": record.get("kind", ""),
                                      "stage": record.get("stage", ""), "occurrences": []})
        entry["count"] += 1
        entry["last_seen"] = record.get("ts", entry["last_seen"])
        entry["occurrences"] = (entry["occurrences"] + [record])[-3:]
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


def _log_entries() -> list[tuple[str, str]]:
    """(heading, body) for each write-up in docs/ERROR_LOG.md."""
    if not ERROR_LOG_PATH.exists():
        return []
    parts = re.split(r"^### +", ERROR_LOG_PATH.read_text(), flags=re.M)[1:]
    return [(p.split("\n", 1)[0].strip(), p.split("\n---", 1)[0].strip()) for p in parts]


def known_writeups(sig: str, limit: int = 2) -> list[tuple[str, str]]:
    """Past write-ups whose heading shares at least two words with `sig`, best
    match first. Headings only: on the real log, matching bodies paired a
    post-length error with the entry about Homebrew, and one shared word paired
    post.deleted with anything mentioning a post. Finding nothing is better
    than misleading the model."""
    words = {w for w in re.findall(r"[a-z_]{4,}", sig.lower()) if w not in _COMMON_WORDS}
    scored = []
    for heading, body in _log_entries():
        shared = len(words & set(re.findall(r"[a-z_]{4,}", heading.lower())))
        if shared >= 2:
            scored.append((shared, heading, body))
    scored.sort(key=lambda s: s[0], reverse=True)
    return [(heading, body) for _, heading, body in scored[:limit]]


def report(min_count: int = 1) -> list[dict[str, Any]]:
    """What a person debugging wants, most frequent first. Both `contentmaster
    errors` and the review page's Errors tab show this list, so its numbering
    means the same in both."""
    rows = [{"signature": sig, **info, "known": known_writeups(sig), "proposal": latest_proposal(sig)}
            for sig, info in error_counts().items() if info["count"] >= min_count]
    return sorted(rows, key=lambda r: (r["count"], r["last_seen"]), reverse=True)


def _proposals() -> dict[str, dict[str, Any]]:
    if not PROPOSALS_PATH.exists():
        return {}
    latest: dict[str, dict[str, Any]] = {}
    for line in PROPOSALS_PATH.read_text().splitlines():
        if line.strip():
            record = json.loads(line)
            latest[record["signature"]] = record
    return latest


def latest_proposal(sig: str) -> dict[str, Any] | None:
    return _proposals().get(sig)


def _append_proposal(record: dict[str, Any]) -> dict[str, Any]:
    PROPOSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PROPOSALS_PATH.open("a") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
    return record


def _fix_prompt(row: dict[str, Any]) -> str:
    prompt = ("A local marketing pipeline keeps hitting the error below. Propose the smallest change "
              "that would stop it happening, rather than a better way of handling it. Say which part "
              "of your answer is a guess. A human will read this and decide; nothing is applied.\n\n"
              f"ERROR: {row['signature']}\nSeen {row['count']} time(s), most recently {row['last_seen']}.\n\n"
              "RECENT OCCURRENCES:\n")
    prompt += "".join(json.dumps(o, default=str)[:600] + "\n" for o in row["occurrences"])
    if row["known"]:
        prompt += "\nWRITE-UPS OF SIMILAR PAST ERRORS:\n" + "\n\n".join(body[:1500] for _, body in row["known"]) + "\n"
    return prompt


def propose_fix(sig: str, min_count: int = 1) -> dict[str, Any] | None:
    """Ask a model about one error. None when the model did not answer: why
    is in the audit log (discuss/model_error), and no empty proposal is kept."""
    from . import discuss

    row = next((r for r in report(min_count) if r["signature"] == sig), None)
    if row is None:
        raise KeyError(f"no error with signature {sig!r}")
    prompt = _fix_prompt(row)
    raw = discuss._complete(FIX_MODEL, prompt, timeout=180, signature=sig)
    if not raw:
        return None
    return _append_proposal({"signature": sig, "model": FIX_MODEL, "prompt": prompt, "raw": raw,
                             "status": "pending", "note": "",
                             "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def decide_proposal(sig: str, accepted: bool, note: str = "") -> dict[str, Any]:
    """Records the human's answer. Accepting says this fix should be made; it
    does not make it."""
    current = latest_proposal(sig)
    if current is None:
        raise KeyError(f"no proposal for {sig!r}")
    return _append_proposal({**current, "status": "accepted" if accepted else "rejected", "note": note,
                             "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
