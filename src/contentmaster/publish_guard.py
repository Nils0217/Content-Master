"""Last check before anything reaches a real account — surface-independent.

2026-09-20. The bug this exists for: on 2026-09-15 a reviewer typed
"add some details" into the terminal's [e]dit prompt, meaning it as an
instruction, and it went out as a live Bluesky post reading literally
`add some details` (draft-e153c8d4 — later deleted by hand, which is how
it turned up again in the era 0 audit).

That was fixed the same day *in the terminal UI*: the [e]dit prompt now
says "not instructions for the LLM" and asks for confirmation, and a
separate [f]eedback option sends instructions to the LLM instead (see
human_loop.review_draft). Three days later Phase 5 added a Streamlit
review UI and made it the default, with an "Edit" box labeled only
"Replacement text" and no confirmation — the same hole, reopened, because
the guard lived in the UI layer and a new UI started from zero.

So the guard lives here instead, called from pipeline.step5_publish(),
which every surface already goes through. A future review UI gets it
without knowing it exists, which is the only property that actually
prevents a third recurrence.

These are heuristics about *shape*, not content judgement — that is the
human reviewer's job and this does not replace it. They are deliberately
tuned to catch "this is obviously not a post" and nothing subtler, and
every one of them can be overridden (see `acknowledged`), because a
genuinely short post has to stay possible.
"""
from __future__ import annotations

# Below this, a "post" is almost certainly a stray note. The shortest
# real post this pipeline has produced is ~85 characters; the accident
# this guards against was 16.
MIN_POST_CHARS = 30

# An edit that collapses a draft to this fraction of its length is far
# more likely to be feedback typed into the wrong box than a rewrite.
SUSPICIOUS_SHRINK_RATIO = 0.4

# Openings that read as an instruction to a writer rather than as a post.
# Matched only on the first word, and only ever a warning on its own —
# "Add a cat tower to any room" is a legitimate post.
FEEDBACK_OPENERS = (
    "add", "make", "shorten", "lengthen", "remove", "delete", "change",
    "rewrite", "revise", "fix", "improve", "include", "mention", "use",
    "try", "avoid", "expand", "simplify", "reword", "tone",
)


class DraftNotPublishable(RuntimeError):
    """The text handed to step5_publish() does not look like a post.

    Deliberately NOT a PlatformAPIError: step5_publish() degrades every
    platform failure into a labeled simulated publish, and that is the
    wrong response here — a simulated publish is *recorded as published*,
    so degrading would file the accident in the ledger instead of stopping
    it. Nothing has been sent when this is raised.
    """


def publish_blockers(final_text: str, original_text: str | None = None,
                     channel: str | None = None) -> list[str]:
    """Reasons `final_text` should not go out as a post. Empty list = fine.

    `original_text` is the draft the human started from, when known — the
    strongest signal available is the ratio between the two, since it is
    what separates "deliberately terse post" from "feedback pasted over a
    full draft".

    `channel` (2026-09-21) brings the platform's own hard rules in here
    too, so the last checkpoint before publishing enforces them rather
    than letting the platform be the thing that discovers them. Note these
    particular blockers are NOT overridable by `acknowledged`: a human
    insisting on 322 characters does not make Bluesky accept 322
    characters.
    """
    text = (final_text or "").strip()
    if not text:
        return ["The post text is empty."]

    blockers = []
    if len(text) < MIN_POST_CHARS:
        blockers.append(
            f"Only {len(text)} characters (minimum {MIN_POST_CHARS}) — this is the length of a "
            f"note, not a post: {text!r}"
        )
    if original_text and len(original_text.strip()) >= MIN_POST_CHARS:
        ratio = len(text) / len(original_text.strip())
        if ratio < SUSPICIOUS_SHRINK_RATIO:
            blockers.append(
                f"Replaces a {len(original_text.strip())}-character draft with {len(text)} "
                f"characters ({ratio:.0%} of it) — if this was meant as instructions for the "
                f"LLM, use the feedback option instead of the edit box."
            )
    if channel:
        try:
            from .platforms.registry import get_platform

            blockers.extend(get_platform(channel).constraints.violations(text))
        except Exception:
            pass  # unimplemented channel: no declared rules to check against
    if blockers and text.split()[0].lower().rstrip(",:") in FEEDBACK_OPENERS:
        blockers.append(
            f"It also starts with {text.split()[0]!r}, which reads as an instruction to a writer "
            "rather than as something an audience would read."
        )
    return blockers
