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
import re
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
    # Deduplicated: two drafts in one batch sharing an axis is the normal,
    # desirable case (that is what makes it a comparison), and reporting
    # it twice as "new" reads like two separate problems.
    return {
        "characteristics": sorted({c for c in proposed_characteristics if c not in chars}),
        "axes": sorted({a for a in proposed_axes if a not in axes}),
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


# How many separate things a control post claims to have contradicted. One
# is a test; three is a redesign nothing can be learned from.
#
# Two is already too many: the whole value of a control is that one thing
# changed, so a control naming two changes is exactly the case worth
# catching. (Briefly set to three while a synthetic test fixture tripped
# it — that was weakening a real check to satisfy fake data, and by three
# changes a post is a rewrite, not a test.)
#
# Counting free text does misfire: "used a statement and got a like" is
# one change plus its result, not two changes. That is acceptable here
# because this prints a note rather than blocking anything, and because
# the instruction now asks the model to name one thing plainly — prose
# vague enough to trip this is itself a sign the control is not crisp.
_SEPARATORS = re.compile(r",|;| and | plus | as well as ", re.IGNORECASE)
MAX_CONTROL_CLAIMS = 1


def _count_claims(text: str) -> int:
    return len([part for part in _SEPARATORS.split(text or "") if len(part.strip()) > 3])


def validate_group(drafts: list[dict[str, Any]]) -> list[str]:
    """Problems with a batch's test axes. Empty list = it is a real test.

    Three things are checked, all of them found by a real post rather than
    imagined:

    1. An axis only tests something if at least two drafts share it and
       sit on different arms.
    2. A draft with no axis at all is not part of any comparison. The
       first post published under this design filled EVIDENCE-AGAINST but
       left TESTING blank, so it could never be grouped with anything —
       and this function said nothing, because it only looked at drafts
       that *had* an axis.
    3. A control that contradicts several things at once isolates
       nothing. That same post went against "tone, hashtags and
       questions" in one go: whatever it scores, there is no way to tell
       which change caused it. The point of a control is one variable.
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

    without_axis = [d for d in drafts if not d.get("test_axis")]
    if without_axis:
        problems.append(
            f"{len(without_axis)} draft(s) name no test axis at all — they cannot be grouped "
            "with anything later, so whatever they score answers no question"
        )

    for d in drafts:
        if not is_control(d):
            continue
        claims = _count_claims(d.get("evidence_against", ""))
        if claims > MAX_CONTROL_CLAIMS:
            problems.append(
                f"a control draft contradicts {claims} things at once "
                f"({d.get('evidence_against', '')[:90]}) — if it performs differently, nothing "
                "says which change did it. A control should vary one thing"
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


def record_from_drafts(drafts: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Fold a batch's labels into the registries, and report which of them
    the vocabulary had never seen.

    Without this the registries are write-never: describe_for_prompt()
    would say "nothing has been recorded yet" on every single run, the
    model would never be shown what it has already used, and it would coin
    a fresh synonym each time — "playful tone", then "casual register",
    then "informal voice" for one idea. That is the whole failure this
    module exists to prevent, and it is only prevented if something
    actually writes.

    Returns the newly-seen names so the caller can put them in front of a
    human. Deliberately records first and asks after: a label the model
    used is a fact about what it did, whether or not a human likes the
    wording, and losing it would leave that draft unclassifiable.
    """
    new = unconfirmed(
        [c for d in drafts for c in (d.get("characteristics") or [])],
        [d["test_axis"] for d in drafts if d.get("test_axis")],
    )
    for draft in drafts:
        for characteristic in draft.get("characteristics") or []:
            record_characteristic(characteristic)
        if draft.get("test_axis"):
            record_axis(draft["test_axis"], draft.get("test_arm", ""))
    return new


def confirm_new_labels(new: dict[str, list[str]]) -> None:
    """Show a human anything the vocabulary has not seen before.

    Not a blocking prompt. A new label is not a risk the way a new product
    name is — it does not split a history, it just adds a word — so this
    reports rather than interrogates, and the human acts on it by editing
    the registry files if a synonym slipped through. The point is that a
    growing vocabulary is visible rather than silent.
    """
    if not any(new.values()):
        return
    print("\n[new vocabulary] The model used labels that have not appeared before:")
    for characteristic in new.get("characteristics") or []:
        print(f"  characteristic: {characteristic}")
    for axis in new.get("axes") or []:
        print(f"  test axis:      {axis}")
    print("  If any of these mean the same thing as an existing label, edit "
          "plays/_characteristics.jsonl or plays/_test_axes.jsonl so they do not "
          "split into two.\n")
