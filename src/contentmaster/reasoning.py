"""Every step of an analysis, kept — prompts, raw replies, what was parsed
from them, and what was chosen.

2026-10-04. Before this, the two advisors' proposals existed only in
audit/events.jsonl with no post_id and no checkpoint, the prompts that
produced them were not stored anywhere, and history kept only the judge's
final text. Working out where a hypothesis had gone missing meant matching
log lines by timestamp. One record per (post, checkpoint) analysis, here,
answers "what was each model asked, what did it say, and what did we do
with it" without guessing.

Read by warehouse/models/staging/stg_reasoning.sql.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from .config import settings

REASONING_PATH = settings.data_root / "plays" / "_reasoning.jsonl"


def new_id() -> str:
    return f"rsn-{uuid.uuid4().hex[:8]}"


def record(reasoning_id: str, *, post_id: str | None, checkpoint: str | None,
           product: str, channel: str, mode: str, steps: list[dict[str, Any]],
           outcome: str, result_text: str, hypothesis: dict[str, Any] | None) -> None:
    """`steps` is the ordered list of model calls: each has `step`
    ("advisor"/"judge"), `role`, `model`, `prompt`, `raw` (the reply as it
    came back, None if the call failed) and `parsed`.

    `outcome` names what happened, so an empty result is never ambiguous:
    "picked" / "only_one_usable" / "judge_unusable" / "none_usable" /
    "no_response" for a hypothesis; "synthesized" / "unmerged" /
    "single_model" / "no_response" for an evidence-based strategy.
    """
    REASONING_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "reasoning_id": reasoning_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "post_id": post_id, "checkpoint": checkpoint,
        "product": product, "channel": channel,
        "mode": mode, "outcome": outcome,
        "steps": steps,
        "result_text": result_text,
        "hypothesis": hypothesis,
    }
    with REASONING_PATH.open("a") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def load(reasoning_id: str) -> dict[str, Any] | None:
    if not reasoning_id or not REASONING_PATH.exists():
        return None
    found = None
    with REASONING_PATH.open() as fh:
        for line in fh:
            line = line.strip()
            if line and f'"{reasoning_id}"' in line:
                row = json.loads(line)
                if row.get("reasoning_id") == reasoning_id:
                    found = row
    return found
