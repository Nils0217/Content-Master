"""Layer 5 — Modiqo.ai (Rote): muscle memory. The first time a
(product, channel) combo succeeds, capture the winning pattern so run #2+
replays it instead of re-generating from scratch — cheaper, faster, and it's
the "proof of compounding" judges look for.

Two things happen on a successful run:
  1. A local deterministic play record is written to plays/*.json — the
     thing pipeline.py actually checks before calling RocketRide again.
  2. `rote play pending write` registers the capture with the real Rote
     workspace (`contentmaster`, see `rote init`) so it survives session
     restarts and can be promoted into a full released Play later via
     `rote play pending save` -> `rote play template create` -> QA ->
     `rote play release`. That promotion step is a deliberate manual
     checkpoint (Rote's own lifecycle wants a human QA pass before a play
     is trusted to replay unattended) — not automated here.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .config import settings

PLAYS_DIR = settings.project_root / "plays"
WORKSPACE = "contentmaster"


def _play_key(product_name: str, channel: str) -> str:
    return f"{product_name}::{channel}".lower().replace(" ", "-")


def _play_path(product_name: str, channel: str) -> Path:
    return PLAYS_DIR / f"{_play_key(product_name, channel)}.json"


def find_play(product_name: str, channel: str) -> dict[str, Any] | None:
    path = _play_path(product_name, channel)
    if path.exists():
        return json.loads(path.read_text())
    return None


def capture_success(
    product_name: str,
    channel: str,
    winning_text: str,
    metrics: dict[str, Any],
    reviewer_note: str = "",
    improvement_note: str = "",
) -> dict[str, Any]:
    """Persist the winning (product, channel) -> text pattern, bumping a run counter."""
    PLAYS_DIR.mkdir(parents=True, exist_ok=True)
    path = _play_path(product_name, channel)
    play = find_play(product_name, channel) or {
        "product": product_name,
        "channel": channel,
        "runs": 0,
    }
    play.update(
        {
            "winning_text": winning_text,
            "last_metrics": metrics,
            "reviewer_note": reviewer_note,
            "improvement_note": improvement_note,
            "runs": play["runs"] + 1,
        }
    )
    path.write_text(json.dumps(play, indent=2))

    _register_with_rote(product_name, channel, metrics)
    return play


def capture_failure(product_name: str, channel: str, text: str, reason: str, improvement_note: str = "") -> None:
    """Failed / rejected runs are logged separately as improvement samples
    (white paper §3, step 7) rather than polluting the muscle-memory file.
    """
    PLAYS_DIR.mkdir(parents=True, exist_ok=True)
    fails_path = PLAYS_DIR / "_failures.jsonl"
    record = {
        "product": product_name,
        "channel": channel,
        "text": text,
        "reason": reason,
        "improvement_note": improvement_note,
    }
    with fails_path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")


def _register_with_rote(product_name: str, channel: str, metrics: dict[str, Any]) -> None:
    name_slug = _play_key(product_name, channel)
    cmd = [
        "rote", "play", "pending", "write", WORKSPACE,
        "--name", name_slug,
        "--description", f"Replay the winning {channel} post pattern for {product_name}",
        "--notes", f"captured by contentmaster pipeline; metrics={json.dumps(metrics)}",
        "--json",
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    except FileNotFoundError:
        pass  # rote CLI not on PATH — local play file still saved above
