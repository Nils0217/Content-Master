"""What has been verified about a platform, for every product on it.

2026-10-30 (docs/SCHEDULE.md Phase 11). A finding about a platform rather
than a product ("posts with links reach fewer people here") used to hang on
the product, so each new product would rediscover it.

Added by a human only, with the evidence stated, and never by a model: a
product-level fact misfiled as a platform fact would poison every other
product. Same append plus last write wins pattern as draft_queue.py. With no
rule on a platform prompt_block() is empty, so every prompt stays as it was.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .config import settings

RULES_PATH = settings.data_root / "plays" / "_platform_rules.jsonl"


def _latest() -> dict[str, dict[str, Any]]:
    if not RULES_PATH.exists():
        return {}
    latest: dict[str, dict[str, Any]] = {}
    for line in RULES_PATH.read_text().splitlines():
        if line.strip():
            record = json.loads(line)
            latest[record["id"]] = record
    return latest


def _append(record: dict[str, Any]) -> dict[str, Any]:
    RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RULES_PATH.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    return record


def add(platform: str, rule: str, evidence: str) -> dict[str, Any]:
    if not (platform.strip() and rule.strip() and evidence.strip()):
        raise ValueError("A platform rule needs its platform, the rule, and the evidence for it.")
    return _append({"id": uuid4().hex[:8], "platform": platform.strip().lower(), "rule": rule.strip(),
                    "evidence": evidence.strip(), "status": "active",
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def retire(rule_id: str) -> dict[str, Any]:
    current = _latest().get(rule_id)
    if current is None:
        raise KeyError(f"no platform rule {rule_id}")
    return _append({**current, "status": "retired",
                    "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def active(platform: str | None = None) -> list[dict[str, Any]]:
    return [r for r in _latest().values() if r["status"] == "active"
            and (platform is None or r["platform"] == platform.lower())]


def prompt_block(platform: str) -> str:
    rules = active(platform)
    if not rules:
        return ""
    return (f"VERIFIED ABOUT {platform.upper()} (true for every product here, checked by a human):\n"
            + "".join(f"- {r['rule']}\n" for r in rules) + "\n")
