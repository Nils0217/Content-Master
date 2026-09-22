"""Layer 5 — muscle memory. The first time a (product, channel) combo
succeeds, capture the winning pattern so the next draft builds on it
instead of re-generating from scratch.

Called from a checkpoint review (see pipeline.py's run_review() /
_finalize_analysis(), triggered by `contentmaster review`) since 2026-09-15, not
right after publish anymore — see docs/SCHEDULE.md Phase 9 / docs/LOG.md
for why (metrics pulled seconds after publish are noise, not signal).

Three things happen on a successful checkpoint:
  1. A local deterministic play record is written to plays/*.json — the
     current-state snapshot draft_generator checks before writing the next
     draft. Overwritten each call — NOT a history (see find_best_prior()
     below for why an overwrite here is fine).
  2. An entry is appended to plays/_history.jsonl, tagged with which
     checkpoint tier (24h/7d/30d) produced it — the actual append-only
     ledger analysis.py cross-validates against *within one tier*
     (also loaded into the warehouse as performance_history — see
     warehouse/models/staging/stg_success_history.sql). Without this,
     "did last run's suggestion help" and "what's this product/channel's
     trend" are both unanswerable — the snapshot file alone only ever has
     one data point.
  3. `rote play pending write` registers the capture with the real Rote
     workspace (`contentmaster`, see `rote init`) so it survives session
     restarts and can be promoted into a full released Play later via
     `rote play pending save` -> `rote play template create` -> QA ->
     `rote play release`. That promotion step is a deliberate manual
     checkpoint (Rote's own lifecycle wants a human QA pass before a play
     is trusted to replay unattended) — not automated here. Best-effort:
     silently skipped if the `rote` CLI isn't on PATH.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings
from .slug import slugify
from .tracking_review import CHECKPOINT_MATURITY_ORDER

PLAYS_DIR = settings.data_root / "plays"
HISTORY_PATH = PLAYS_DIR / "_history.jsonl"
WORKSPACE = "contentmaster"


def _play_key(product_name: str, channel: str) -> str:
    """2026-09-17 (code scan finding): used to only lowercase + replace
    spaces — any other special character (a real product name this
    session had a comma in it) passed straight into a file path
    unsanitized, unlike topic_index.py/pipeline.py's stricter slugify.
    Now shares the same `slugify()` — confirmed safe to change: zero
    `plays/*.json` snapshot files existed when this changed, so no
    existing file's name shifted under it.
    """
    return f"{slugify(product_name)}::{slugify(channel)}"


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
    checkpoint: str = "24h",
) -> dict[str, Any]:
    """Persist the winning (product, channel) -> text pattern, bumping a run counter.

    `analysis` (see analysis.AnalysisResult.as_dict()) is the track ->
    ANALYSIS -> log -> improve step's verdict this run's `improvement_note`
    (really: discuss.py's multi-model synthesized strategy) was built on —
    stored so the *next* analysis pass, and any human reviewing this file,
    can see the reasoning, not just its conclusion.

    `checkpoint` (2026-09-15, docs/SCHEDULE.md Phase 9): since this now
    fires once per checkpoint tier (24h/7d/30d) instead of once right
    after publish, plays/*.json ends up holding whichever checkpoint most
    recently ran — that's fine, it's meant to be "current state", not
    history (see module docstring). The full 24h->7d->30d progression for
    a single post lives in plays/_history.jsonl instead, tagged with
    `checkpoint` per row — that's why _append_history() needs it too.
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
            "checkpoint": checkpoint,
            "runs": play["runs"] + 1,
        }
    )
    path.write_text(json.dumps(play, indent=2, default=str))
    _append_history(product_name, channel, winning_text, metrics, reviewer_note, improvement_note,
                     analysis, play["runs"], checkpoint, outcome="success")

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
    checkpoint: str,
    outcome: str,
) -> None:
    """Append-only — never overwritten, unlike plays/*.json. This is what
    makes real cross-run analysis possible at all. One row per
    (post, checkpoint) now — `checkpoint` is what lets analysis.py compare
    only within the same tier (24h vs 24h, never 24h vs 7d — those answer
    different questions, see docs/SCHEDULE.md Phase 9).

    `outcome` (2026-09-18): "success" or "failure". Before this, only
    capture_success() ever called this function, so a product/channel that
    never once cleared SUCCESS_CTR_THRESHOLD had zero rows here forever —
    every checkpoint kept reporting "no_history" even after several real
    posts, and every failed checkpoint's improvement_note (real LLM
    reasoning) was silently thrown away instead of reaching the next
    draft. capture_failure() now appends here too, tagged "failure", so
    both problems are fixed at once: analysis.py's n_prior_posts/trend see
    the real count, and find_best_prior() can read a failure's
    improvement_note when nothing better exists yet.
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
        "checkpoint": checkpoint,
        "outcome": outcome,
    }
    with HISTORY_PATH.open("a") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def comparable_history(channel: str, checkpoint: str,
                       exclude_post_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Raw count dicts for prior posts that this post can legitimately be
    compared against — scoring.judge()'s baseline.

    Filtered to the same channel and the same checkpoint tier (24h only
    ever compares against 24h). Rows whose `metrics` predate raw-count
    storage are skipped rather than treated as zeros: under the weighted
    sum a missing count and a real zero are indistinguishable in the
    score but mean completely different things, and silently counting old
    rows as zeros would drag the baseline percentile to the floor exactly
    when the account starts getting real engagement.

    NOT yet filtered by account era or by the product/document identity —
    both columns are still being added (docs/SCHEDULE.md Phase 10). When
    they land they belong here AND in
    warehouse/models/staging/stg_success_history.sql: filtering in Python
    only leaks silently into the marts with no error.
    """
    if not HISTORY_PATH.exists():
        return []
    exclude = exclude_post_ids or set()
    rows = []
    with HISTORY_PATH.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("channel") != channel or record.get("checkpoint") != checkpoint:
                continue
            metrics = record.get("metrics") or {}
            if "like_count" not in metrics:
                continue  # predates raw-count storage — cannot be scored
            # `engagement_score` may be absent on a row written during a
            # field-shape change; scoring.score_of() recomputes it from the
            # raw counts rather than letting it read as 0.
            if metrics.get("post_id") in exclude:
                continue
            rows.append(metrics)
    return rows


def find_best_prior(
    product_name: str, channel: str, prefer: tuple[str, ...] = CHECKPOINT_MATURITY_ORDER,
) -> dict[str, Any] | None:
    """draft_generator's cold-start fallback (decided via /grill-me,
    2026-09-15): the immediately-prior post may only have a shallow (24h)
    reading, while an earlier post already has a more mature (7d) one —
    walk plays/_history.jsonl and prefer the most recent entry at the
    highest-maturity tier available, not just whichever is most recent
    overall. Returns None if nothing at all exists yet (true cold start —
    draft_generator writes ungrounded in that case, same as before).

    2026-09-19 (code scan): `prefer` was hardcoded to ("7d", "24h") —
    30d, the *most* mature tier, was missing entirely, contradicting this
    docstring. Meanwhile tracking_review.py had already defined the
    correct order as CHECKPOINT_MATURITY_ORDER with a comment saying "this
    is what find_best_prior prefers", and nothing ever imported it. A post
    whose only completed checkpoint was 30d was treated as a cold start.
    Now sourced from that one constant, so the two cannot drift again.

    Shaped like find_play()'s return value (`winning_text`,
    `last_metrics`, `improvement_note`) so draft_generator.py doesn't need
    to care which of the two functions supplied `prior`. Also carries
    `human_feedback` (2026-09-16) — when a human disagreed with the
    combined analysis+recommendation (see analysis.confirm_with_human()),
    their note is stored *alongside* the LLM's improvement_note, not in
    place of it; draft_generator.py's prompt includes both and is told to
    prioritize the human's direction where they conflict.
    """
    if not HISTORY_PATH.exists():
        return None
    best_by_tier: dict[str, dict[str, Any]] = {}
    with HISTORY_PATH.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("product") != product_name or record.get("channel") != channel:
                continue
            tier = record.get("checkpoint")
            if tier is None:
                continue  # pre-dates this field — can't tell its maturity, skip rather than guess
            # later lines are later in time, so the last one seen per tier is the most recent
            best_by_tier[tier] = record

    for tier in prefer:
        record = best_by_tier.get(tier)
        if record:
            analysis = record.get("analysis") or {}
            return {
                "winning_text": record.get("winning_text", ""),
                "last_metrics": record.get("metrics", {}),
                "improvement_note": record.get("improvement_note", ""),
                "human_feedback": analysis.get("human_note") or "",
                "checkpoint": tier,
            }
    return None


def capture_failure(
    product_name: str,
    channel: str,
    text: str,
    reason: str,
    improvement_note: str = "",
    analysis: dict[str, Any] | None = None,
    checkpoint: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> None:
    """Failed / rejected runs are logged separately as improvement samples
    (white paper §3, step 7) rather than polluting the muscle-memory file.

    `checkpoint` is None for a draft rejected at human review (never
    published, so no checkpoint tier applies) and set to "24h"/"7d"/"30d"
    when a *published* post's checkpoint reading came in under
    SUCCESS_CTR_THRESHOLD (see pipeline.py's _finalize_analysis) —
    same success/failure split pipeline.py already used right after
    publish, just relocated to checkpoint time.

    2026-09-18: a checkpoint failure (real `checkpoint` set) now also
    appends to plays/_history.jsonl, tagged outcome="failure" — see
    _append_history()'s docstring for why. A rejected-at-review draft
    (checkpoint=None) never published at all, so there's no real
    metrics/checkpoint tier to log there; it stays in _failures.jsonl only.
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
        "checkpoint": checkpoint,
    }
    with fails_path.open("a") as fh:
        fh.write(json.dumps(record, default=str) + "\n")

    if checkpoint:
        _append_history(product_name, channel, text, metrics or {}, "", improvement_note,
                         analysis, 0, checkpoint, outcome="failure")


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
    except (subprocess.TimeoutExpired, OSError) as e:
        # 2026-09-19 (code scan): only FileNotFoundError was caught, so a
        # `rote` binary that hung past the 20s timeout (TimeoutExpired) or
        # was present-but-unrunnable (OSError, e.g. a bad exec bit) raised
        # straight out of capture_success() — losing the checkpoint
        # capture that had already been written to disk just above, over
        # a step whose own docstring calls itself best-effort.
        print(f"[warn] Could not register this play with rote ({e}); the local play file and "
              "plays/_history.jsonl were still written.")
