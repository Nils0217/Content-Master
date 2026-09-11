"""Layer — Human-in-the-loop brand-safety gate (white paper §4.1).

Every draft goes through here before anything is published. A human
reviews the text (and why it was generated), then approves as-is, edits it,
or rejects it. The before/after is what Modiqo later uses to tell a
"success" pattern from one that needed a human fix.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ReviewDecision:
    approved: bool
    final_text: str
    edited: bool
    reviewer_note: str = ""


def review_draft(draft: dict[str, Any], context: dict[str, Any] | None = None) -> ReviewDecision:
    """Blocking CLI review. Swap this for a real queue/UI later — the
    interface (one draft in, one ReviewDecision out) stays the same.
    """
    print("\n" + "=" * 60)
    print(f"DRAFT  {draft['id']}  ·  channel={draft['channel']}")
    if context:
        for k, v in context.items():
            print(f"  context.{k}: {v}")
    print("-" * 60)
    print(draft["text"])
    print("=" * 60)

    choice = input("[a]pprove / [e]dit / [r]eject > ").strip().lower()
    if choice.startswith("e"):
        new_text = input("New text: ").strip() or draft["text"]
        note = input("Why the edit? (for Modiqo's learning signal): ").strip()
        return ReviewDecision(approved=True, final_text=new_text, edited=True, reviewer_note=note)
    if choice.startswith("a"):
        return ReviewDecision(approved=True, final_text=draft["text"], edited=False)
    note = input("Why rejected?: ").strip()
    return ReviewDecision(approved=False, final_text=draft["text"], edited=False, reviewer_note=note)
