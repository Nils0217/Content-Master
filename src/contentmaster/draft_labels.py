"""What a draft IS, and what it is TESTING — recorded by the LLM itself.

2026-09-21, /grill-me design session. Two registries, not one, because
the two things have different shapes and different validity rules:

  characteristics   what this draft is — "opens with a question", "has
                    hashtags", "references a trend". Descriptive, several
                    per draft, observable after the fact.
  test axis + arm   what this draft is testing — an axis ("opening
                    style") and which side of it this draft sits on
                    ("question" / "statement"). Only means anything when
                    another draft in the same group shares the axis and
                    sits on a different arm; a lone "I am testing X" is a
                    wish, not a test.

Nobody writes the vocabulary in advance. The human does not prescribe
"statement vs question" — that is just one person's guess at what matters,
and prescribing it is what stops the system discovering anything else.
The LLM proposes labels; they are recorded; from then on it is shown what
already exists and must either reuse a label or say plainly that it is
proposing a new one.

That second part is not optional. Left completely free, a model writes
"playful tone", then "casual register", then "informal voice" — three
names for one thing, and nothing can be grouped or compared. It is the
same failure as a hand-typed product name splitting one history four
ways (see product_registry.py), one level up. A new label therefore needs
one human confirmation, exactly as a new topic does in topic_index.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .config import settings

CHARACTERISTICS_PATH = settings.data_root / "plays" / "_characteristics.jsonl"
TEST_AXES_PATH = settings.data_root / "plays" / "_test_axes.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load(path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("name"):
            out[record["name"]] = record
    return out


def _append(path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def known_characteristics() -> list[str]:
    return sorted(_load(CHARACTERISTICS_PATH))


def known_axes() -> dict[str, list[str]]:
    """Axis name -> the arms seen on it so far."""
    return {name: list(r.get("arms") or []) for name, r in _load(TEST_AXES_PATH).items()}


def record_characteristic(name: str, confirmed_by_human: bool = False) -> dict[str, Any]:
    existing = _load(CHARACTERISTICS_PATH).get(name)
    record = {
        "name": name,
        "first_seen": (existing or {}).get("first_seen", _now()),
        "last_used": _now(),
        "times_used": (existing or {}).get("times_used", 0) + 1,
        "confirmed": (existing or {}).get("confirmed", False) or confirmed_by_human,
    }
    _append(CHARACTERISTICS_PATH, record)
    return record


def record_axis(name: str, arm: str, confirmed_by_human: bool = False) -> dict[str, Any]:
    existing = _load(TEST_AXES_PATH).get(name)
    arms = list((existing or {}).get("arms") or [])
    if arm and arm not in arms:
        arms.append(arm)
    record = {
        "name": name,
        "arms": arms,
        "first_seen": (existing or {}).get("first_seen", _now()),
        "last_used": _now(),
        "times_used": (existing or {}).get("times_used", 0) + 1,
        "confirmed": (existing or {}).get("confirmed", False) or confirmed_by_human,
    }
    _append(TEST_AXES_PATH, record)
    return record


def unconfirmed(proposed_characteristics: list[str], proposed_axes: list[str]) -> dict[str, list[str]]:
    """Which of these the registries have never seen — the ones a human
    has to look at before they become part of the vocabulary.
    """
    chars, axes = _load(CHARACTERISTICS_PATH), _load(TEST_AXES_PATH)
    return {
        "characteristics": [c for c in proposed_characteristics if c not in chars],
        "axes": [a for a in proposed_axes if a not in axes],
    }


def describe_for_prompt() -> str:
    """The existing vocabulary, shown to the LLM so it reuses rather than
    reinvents. Explicitly permits proposing something new — the point is
    to stop silent synonyms, not to stop new ideas.
    """
    chars, axes = known_characteristics(), known_axes()
    if not chars and not axes:
        return ("No characteristics or test axes have been recorded yet. Propose whatever you "
                "think describes these drafts and whatever you think is worth testing.")
    parts = []
    if chars:
        parts.append("Characteristics used before: " + ", ".join(chars) + ".")
    if axes:
        parts.append("Test axes used before: "
                     + "; ".join(f"{name} ({' vs '.join(arms)})" for name, arms in axes.items())
                     + ".")
    parts.append("Reuse one of these EXACT names when it fits. If none does, propose a new name "
                 "and mark it as new — do not invent a synonym for something already listed.")
    return " ".join(parts)


def validate_group(drafts: list[dict[str, Any]]) -> list[str]:
    """Problems with a batch's test axes. Empty list = it is a real test.

    The rule being checked: an axis only tests something if at least two
    drafts share it and sit on different arms.
    """
    problems = []
    by_axis: dict[str, set[str]] = {}
    for d in drafts:
        axis, arm = d.get("test_axis"), d.get("test_arm")
        if axis:
            by_axis.setdefault(axis, set()).add(arm or "")
    for axis, arms in by_axis.items():
        if len(arms) < 2:
            problems.append(
                f"axis {axis!r} has every draft on the same side ({', '.join(sorted(arms))}) — "
                "nothing is being compared, so it tests nothing"
            )
    return problems


def is_control(record: dict[str, Any]) -> bool:
    """Did this draft deliberately contradict the evidence?"""
    value = (record.get("evidence_against") or "").strip().lower()
    return bool(value) and value not in ("none", "n/a", "-", "nothing")


def controls_needed(product: str, channel: str, batch_size: int) -> int:
    """How many of THIS batch must contradict the evidence.

    The quota belongs to a test group, not to a batch. A group is
    scoring.MIN_BASELINE_POSTS drafts — the same window a baseline is
    built from — and scoring.control_arm_size() of them must be controls.
    A batch is usually 1 (`contentmaster run` defaults to --posts 1), and
    20% of 1 rounds to nothing, so a per-batch quota would silently never
    fire. The shortfall is therefore carried across runs.

    Read as a rolling guarantee: across the most recent `group_size`
    drafts *including this batch*, at least `required` are controls.
    """
    from .scoring import MIN_BASELINE_POSTS, control_arm_size
    from . import draft_queue

    group_size = MIN_BASELINE_POSTS
    required = control_arm_size(group_size)

    recent = [r for r in draft_queue._latest_entries().values()
              if r.get("channel") == channel and r.get("product") == product]
    recent.sort(key=lambda r: r.get("ts", ""))
    # The part of the window this batch will share with existing drafts.
    carried = recent[-max(0, group_size - batch_size):] if group_size > batch_size else []
    have = sum(1 for r in carried if is_control(r))
    return max(0, min(batch_size, required - have))
