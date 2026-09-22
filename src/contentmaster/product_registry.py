"""Which product names have been used before, and how often.

2026-09-20. The problem: `product_name` is hand-typed on every run and is
the key everything else hangs off — plays/<product>::<channel>.json, the
topic index filename, the (product, channel) history rows. The same cat
whitepaper was typed as "trouble shoot", "Fluffy roomate", "How to Use a
Cat" and "Your best stress reliever" across 12 posts, so the history
split four ways and every analysis reported "no prior posts" no matter
how much had actually been published. plays/_history.jsonl holds 4
products across 6 rows; n was never above 1.

This does NOT rename anything or pick a name for you — the name is a
human decision and stays one. It just makes the consequence visible at
the moment of typing: say how many times a name has been used and when,
and say loudly when a name has never been seen before, since that is
what a typo looks like. A human seeing "never used before" next to four
near-identical existing names will notice; a human seeing nothing will
not, which is exactly what happened.

Deliberately never written by an LLM. Draft text is generated, product
identity is not — an LLM that could coin a product name would recreate
the fragmentation this exists to stop, one synonym at a time.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .config import settings

REGISTRY_PATH = settings.data_root / "plays" / "_products.jsonl"


def _latest() -> dict[str, dict[str, Any]]:
    """Last write wins per name — same append-only read pattern as
    metrics_store and tracking_review.
    """
    if not REGISTRY_PATH.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with REGISTRY_PATH.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("name"):
                out[record["name"]] = record
    return out


def known_names() -> list[str]:
    return sorted(_latest())


def lookup(name: str) -> dict[str, Any] | None:
    return _latest().get(name)


def describe(name: str) -> str:
    """The one-line reminder shown before a run starts."""
    record = lookup(name)
    if record is None:
        existing = known_names()
        msg = f"'{name}' has never been used before — this starts a brand-new history for it."
        if existing:
            msg += ("\n         Names already in use: " + ", ".join(repr(n) for n in existing) +
                    "\n         If you meant one of those, stop and re-run with that exact "
                    "spelling — a near-miss silently starts a second history that never "
                    "accumulates enough posts to compare against.")
        return msg
    times = record.get("times_used", 0)
    return (f"'{name}' has been used {times} time(s), most recently "
            f"{record.get('last_used', '?')[:16]}.")


def remember(name: str, source_hash: str | None = None, whitepaper: str | None = None) -> dict[str, Any]:
    """Records one use of `name`. Called once per run, after the human has
    seen describe()'s reminder and gone ahead anyway.

    `source_hash` is the whitepaper's content hash when known
    (topic_index.file_hash) — collected now so that the move to a
    document-based identity later has the name<->document mapping it
    needs, without having to reconstruct it from git history.
    """
    prior = lookup(name)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    hashes = list((prior or {}).get("source_hashes") or [])
    if source_hash and source_hash not in hashes:
        hashes.append(source_hash)
    record = {
        "name": name,
        "first_seen": (prior or {}).get("first_seen", now),
        "last_used": now,
        "times_used": (prior or {}).get("times_used", 0) + 1,
        "source_hashes": hashes,
        "whitepaper": whitepaper or (prior or {}).get("whitepaper"),
    }
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REGISTRY_PATH.open("a") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
    return record
