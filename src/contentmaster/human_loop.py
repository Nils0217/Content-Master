"""Layer — Human-in-the-loop brand-safety gate (white paper §4.1).

Every draft goes through here before anything is published. A human
reviews the text (and why it was generated), then approves as-is, asks
the LLM to revise it, edits it directly, or rejects it. The before/after
is what Modiqo later uses to tell a "success" pattern from one that
needed a human fix.

2026-09-15 (docs/LOG.md — real production bug): the old [e]dit prompt
just asked "New text:" and used whatever was typed *verbatim* as the
final post. A reviewer typed feedback ("add some details") intending it
as an instruction, and it went out published as literally that string —
a real broken post on a real account. Fixed by splitting the one
ambiguous option into two clearly different ones: [e]dit (you type the
exact final text yourself, now with a confirmation step before it
commits) and [f]eedback (you describe what should change, the LLM
rewrites the draft, you review the result — this is what "add some
details" should have gone through).

2026-09-16 (Phase 4): `review_image()` extends the same principle to
generated images — see image_generator.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


class ReviewInterrupted(Exception):
    """Raised when stdin closes (Ctrl+D) or the user hits Ctrl+C mid-review —
    lets the pipeline stop cleanly and still print a summary of whatever was
    already processed, instead of an unhandled traceback.
    """


@dataclass
class ReviewDecision:
    approved: bool
    final_text: str
    edited: bool
    reviewer_note: str = ""


def review_draft(
    draft: dict[str, Any],
    context: dict[str, Any] | None = None,
    regenerate_fn: Callable[[str, str], str | None] | None = None,
) -> ReviewDecision:
    """Blocking CLI review. Swap this for a real queue/UI later — the
    interface (one draft in, one ReviewDecision out) stays the same.

    `regenerate_fn(current_text, feedback) -> revised_text | None` —
    when given, offers a [f]eedback option: the human describes what
    should change, `regenerate_fn` (see pipeline.py's step4_human_review,
    which binds this to draft_generator.revise_post) sends it to the LLM,
    and the revised draft loops back through this same review — not a
    one-shot literal replacement. `None` from `regenerate_fn` means the
    LLM call failed; the draft is left unchanged and the reviewer is told
    to try again or fall back to [e]dit/[r]eject.
    """
    current_text = draft["text"]
    feedback_notes: list[str] = []
    valid = {"a", "e", "r"} | ({"f"} if regenerate_fn else set())
    options = "[a]pprove / [e]dit / [r]eject"
    if regenerate_fn:
        options = "[a]pprove / [e]dit / [f]eedback (ask the LLM to revise) / [r]eject"

    while True:
        print("\n" + "=" * 60)
        print(f"DRAFT  {draft['id']}  ·  channel={draft['channel']}")
        if context:
            for k, v in context.items():
                print(f"  context.{k}: {v}")
        print("-" * 60)
        print(current_text)
        print("=" * 60)
        try:
            choice = input(f"{options} > ").strip().lower()
        except (EOFError, KeyboardInterrupt) as e:
            raise ReviewInterrupted("Review interrupted (stdin closed or Ctrl+C)") from e

        if choice not in valid:
            print(f"{choice!r} isn't a valid choice — enter one of: {', '.join(sorted(valid))}.")
            continue

        if choice == "a":
            return ReviewDecision(
                approved=True, final_text=current_text, edited=bool(feedback_notes),
                reviewer_note="; ".join(feedback_notes),
            )

        if choice == "e":
            try:
                new_text = input("Type the FULL replacement post text (not instructions for the LLM): ").strip()
            except (EOFError, KeyboardInterrupt) as e:
                raise ReviewInterrupted("Review interrupted (stdin closed or Ctrl+C)") from e
            if not new_text:
                print("Empty — keeping the current text, nothing changed.")
                continue
            print(f"\nAbout to use this as the final text:\n  {new_text}")
            try:
                confirm = input("Confirm? [y]es / [n]o, go back > ").strip().lower()
            except (EOFError, KeyboardInterrupt) as e:
                raise ReviewInterrupted("Review interrupted (stdin closed or Ctrl+C)") from e
            if confirm != "y":
                continue
            try:
                note = input("Why the edit? (for Modiqo's learning signal): ").strip()
            except (EOFError, KeyboardInterrupt) as e:
                raise ReviewInterrupted("Review interrupted (stdin closed or Ctrl+C)") from e
            return ReviewDecision(approved=True, final_text=new_text, edited=True, reviewer_note=note)

        if choice == "f":
            try:
                feedback = input("What should change? (sent to the LLM to rewrite this draft): ").strip()
            except (EOFError, KeyboardInterrupt) as e:
                raise ReviewInterrupted("Review interrupted (stdin closed or Ctrl+C)") from e
            if not feedback:
                print("Empty feedback — nothing sent, draft unchanged.")
                continue
            revised = regenerate_fn(current_text, feedback)  # type: ignore[misc] — only reachable when set
            if revised is None:
                print("[warn] The LLM revision failed — draft unchanged. Try [f]eedback again, "
                      "or use [e]dit/[r]eject instead.")
                continue
            current_text = revised
            feedback_notes.append(feedback)
            continue  # loop back and show the revised draft

        # choice == "r"
        try:
            note = input("Why rejected?: ").strip()
        except (EOFError, KeyboardInterrupt) as e:
            raise ReviewInterrupted("Review interrupted (stdin closed or Ctrl+C)") from e
        return ReviewDecision(
            approved=False, final_text=current_text, edited=bool(feedback_notes),
            reviewer_note="; ".join(feedback_notes + ([note] if note else [])),
        )


def review_image(
    image_bytes: bytes,
    prompt: str,
    regenerate_fn: Callable[[str], bytes | None] | None = None,
    draft_id: str = "",
) -> bytes | None:
    """2026-09-16, Phase 4 (image_generator.py) — same brand-safety
    principle as review_draft(): nothing generated gets attached to a
    real post without a human looking at it first. A terminal can't
    preview an image (see docs/WHITEPAPER.md §3, Phase 5 — Streamlit/
    Gradio is the eventual fix for that), so this saves it to disk and
    asks the reviewer to open it themselves before deciding. Returns the
    approved image bytes, or None if skipped (post goes out text-only —
    never blocks the whole pipeline over an image).

    2026-09-16 (later, real usage): originally used `tempfile`'s default
    location — macOS's per-boot, per-user temp folder
    (`/var/folders/.../T/`), with a random `tmpXXXXXXXX.png` name. A real
    reviewer couldn't find the file afterward. Now saves to a fixed,
    visible project folder (`generated_images/`, gitignored) named after
    the draft, e.g. `draft-e153c8d4.png` — same path every time this
    draft's image is reviewed/regenerated, easy to locate.
    """
    from .config import settings

    out_dir = settings.project_root / "generated_images"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{draft_id or 'draft'}.png"

    valid = {"a", "s"} | ({"r"} if regenerate_fn else set())
    options = "[a]ttach / [s]kip (text-only)"
    if regenerate_fn:
        options = "[a]ttach / [r]egenerate / [s]kip (text-only)"

    while True:
        path.write_bytes(image_bytes)
        print("\n" + "=" * 60)
        print("GENERATED IMAGE")
        print("-" * 60)
        print(f"Saved to: {path}")
        print("Open it yourself to look before deciding (e.g. `open <path>` on Mac).")
        print("=" * 60)
        try:
            choice = input(f"{options} > ").strip().lower()
        except (EOFError, KeyboardInterrupt) as e:
            raise ReviewInterrupted("Image review interrupted (stdin closed or Ctrl+C)") from e

        if choice not in valid:
            print(f"{choice!r} isn't a valid choice — enter one of: {', '.join(sorted(valid))}.")
            continue
        if choice == "a":
            return image_bytes
        if choice == "s":
            return None

        # choice == "r"
        new_image = regenerate_fn(prompt)  # type: ignore[misc] — only reachable when set
        if new_image is None:
            print("[warn] Image regeneration failed — still showing the previous image.")
            continue
        image_bytes = new_image
        continue
