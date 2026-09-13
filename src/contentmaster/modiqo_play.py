"""Layer 5 — Modiqo.ai (Rote): muscle memory. The first time a
(product, channel) combo succeeds, capture the winning pattern so run #2+
replays it instead of re-generating from scratch — cheaper, faster, and it's
the "proof of compounding" judges look for.

Three things happen on a successful run:
  1. A local deterministic play record is written to plays/*.json — the
     current-state snapshot pipeline.py checks before calling RocketRide
     again. Overwritten each run — NOT a history.
  2. An entry is appended to plays/_history.jsonl — the actual append-only
     ledger analysis.py cross-validates against (also loaded into the
     warehouse as performance_history — see warehouse/models/staging/
     stg_success_history.sql). Without this, "did last run's suggestion
     help" and "what's this product/channel's trend" are both unanswerable
     — the snapshot file alone only ever has one data point.
  3. `rote play pending write` registers the capture with the real Rote
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings

PLAYS_DIR = settings.project_root / "plays"
HISTORY_PATH = PLAYS_DIR / "_history.jsonl"
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
    analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist the winning (product, channel) -> text pattern, bumping a run counter.

    `analysis` (see analysis.AnalysisResult.as_dict()) is the track ->
    ANALYSIS -> log -> improve step's verdict this run's `improvement_note`
    (really: discuss.py's multi-model synthesized strategy) was built on —
    stored so the *next* analysis pass, and any human reviewing this file,
    can see the reasoning, not just its conclusion.
    """
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
            "analysis": analysis,
            "runs": play["runs"] + 1,
        }
    )
    path.write_text(json.dumps(play, indent=2, default=str))
    _append_history(product_name, channel, winning_text, metrics, reviewer_note, improvement_note,
                     analysis, play["runs"])

    _register_with_rote(product_name, channel, metrics)
    return play


def _append_history(
    product_name: str,
    channel: str,
    winning_text: str,
    metrics: dict[str, Any],
    reviewer_note: str,
    improvement_note: str,
    analysis: dict[str, Any] | None,
    run_number: int,
) -> None:
    """Append-only — never overwritten, unlike plays/*.json. This is what
    makes real cross-run analysis possible at all.
    """
    PLAYS_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "product": product_name,
        "channel": channel,
        "winning_text": winning_text,
        "metrics": metrics,
        "reviewer_note": reviewer_note,
        "improvement_note": improvement_note,
        "analysis": analysis,
        "run_number": run_number,
    }
    with HISTORY_PATH.open("a") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def capture_failure(
    product_name: str,
    channel: str,
    text: str,
    reason: str,
    improvement_note: str = "",
    analysis: dict[str, Any] | None = None,
) -> None:
    """Failed / rejected runs are logged separately as improvement samples
    (white paper §3, step 7) rather than polluting the muscle-memory file.
    """
    PLAYS_DIR.mkdir(parents=True, exist_ok=True)
    fails_path = PLAYS_DIR / "_failures.jsonl"
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "product": product_name,
        "channel": channel,
        "text": text,
        "reason": reason,
        "improvement_note": improvement_note,
        "analysis": analysis,
    }
    with fails_path.open("a") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


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
