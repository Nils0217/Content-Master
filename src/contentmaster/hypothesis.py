"""What to test next when there is no evidence yet — parsed, rendered, and
kept in one shape for every place that reads it.

2026-10-04. The design said: with no baseline, propose a hypothesis and
write posts that test it. In practice the advisors did propose one, then
the judge was asked for "ONE final strategy" and rewrote it into a plan
("Final strategy: ...") — dropping the test — and the drafting step never
saw it at all. Nothing was tested; a guess built on Google Trends alone was
shown to the reviewer as if it were a strategy.

Three things changed, and they live here so both review surfaces and the
drafting prompt share one definition:

- A hypothesis is TEST / WHY / WATCH. There is no "what would show you
  were wrong" field: a test that does not move its number says the change
  did not suit the conditions at the time, not that the idea was false. The
  conditions are recorded by the system at publish (see `environment`), not
  guessed by the model.
- WATCH may only name a number the system can actually read. The first
  real run's falsifiers cited ad clicks, bounce rate and regional
  engagement — none of which exist here.
- The judge picks A or B and gives a reason. It does not rewrite either
  proposal, so what is tested is exactly what an advisor proposed.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

# The only engagement numbers any platform adapter returns (see
# scoring.engagement_score). Order is the order they are named in prompts.
METRICS = ("likes", "reposts", "replies", "bookmarks")

# Words a model uses for each number. "shares" is how two of four real
# replies described reposts.
_METRIC_WORDS = (
    ("likes", r"likes?"),
    ("reposts", r"reposts?|re-posts?|shares?|reshares?"),
    ("replies", r"repl(?:y|ies)|comments?"),
    ("bookmarks", r"bookmarks?|saves?"),
)

# Field name at line start, after any decoration a model adds to lists —
# bullets, numbering, bold, quote carets, stray dashes. See
# .claude/skills/parse-what-models-return.
_FIELD = re.compile(r"^[\s\-*>#\d.)]*\**\s*(TEST|WHY|WATCH|PICK|REASON)\s*\**\s*:\s*\**\s*(.*)$",
                    re.IGNORECASE)


def _fields(text: str) -> dict[str, str]:
    """Field name -> value. A line with no field name continues the field
    above it (models wrap long answers)."""
    found: dict[str, str] = {}
    last = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        m = _FIELD.match(line)
        if m:
            last = m.group(1).upper()
            if last not in found:  # first answer wins; a model repeating itself is noise
                found[last] = m.group(2).strip().strip("*").strip()
            else:
                last = None
        elif line and last:
            found[last] = (found[last] + " " + line).strip()
    return found


def watch_metric(text: str) -> str | None:
    """The first of METRICS that `text` names, or None.

    Real replies: "100 likes", "reposts", "Reposts (shares) as this trend
    might...", "Likes or reposts as an indicator". The first named number
    is taken; the raw text is kept beside it so nothing is lost.
    """
    best: tuple[int, str] | None = None
    for name, pattern in _METRIC_WORDS:
        m = re.search(rf"\b(?:{pattern})\b", text or "", re.IGNORECASE)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), name)
    return best[1] if best else None


def parse_hypothesis(text: str | None) -> dict[str, Any] | None:
    """{test, why, watch, watch_raw} from an advisor's reply, or None when
    there is no TEST in it. `watch` is None when the reply named no number
    the system can read — kept, and shown, rather than dropped."""
    f = _fields(text or "")
    if not f.get("TEST"):
        return None
    return {
        "test": f["TEST"],
        "why": f.get("WHY", ""),
        "watch": watch_metric(f.get("WATCH", "")),
        "watch_raw": f.get("WATCH", ""),
    }


def parse_pick(text: str | None) -> tuple[str | None, str]:
    """("A" | "B" | None, reason) from the judge's reply."""
    f = _fields(text or "")
    m = re.match(r"\W*(?:advisor\s+)?([AB])\b", f.get("PICK", ""), re.IGNORECASE)
    return (m.group(1).upper() if m else None), f.get("REASON", "")


def new_id() -> str:
    return f"hyp-{uuid.uuid4().hex[:8]}"


def render(h: dict[str, Any] | None) -> str:
    """The stored text form — what history's `improvement_note` and the
    review queue's `recommendation` hold."""
    if not h:
        return ""
    watch = h.get("watch") or f"(not a number this system reads: {h.get('watch_raw') or 'none given'})"
    return f"TEST: {h.get('test', '')}\nWHY: {h.get('why', '')}\nWATCH: {watch}"


def summary_line(h: dict[str, Any] | None) -> str:
    """One line for a reviewer. Both review surfaces print this."""
    if not h:
        return ""
    watch = h.get("watch") or "no readable number"
    line = f"Test: {h.get('test', '')}  |  Watch: {watch}"
    if h.get("picked"):
        line += f"  |  Advisor {h['picked']}"
    return line


def describe_environment(env: dict[str, Any] | None) -> str:
    """The conditions a post went out under, in one line."""
    if not env:
        return "conditions not recorded"
    parts = []
    if env.get("followers") is not None:
        parts.append(f"{env['followers']} followers")
    if env.get("weekday") and env.get("local_hour") is not None:
        parts.append(f"{env['weekday']} {env['local_hour']:02d}:00")
    media = "video" if env.get("has_video") else "image" if env.get("has_image") else "text only"
    parts.append(media)
    if env.get("trends_as_of"):
        parts.append(f"trends from {env['trends_as_of']}")
    return ", ".join(parts)


def review_view(h: dict[str, Any] | None, row: dict[str, Any] | None) -> dict[str, Any]:
    """What a reviewer sees, decided once for both surfaces.

    `row` is the plays/_reasoning.jsonl record. Returns:
      headline   — the one line (empty when there is no hypothesis)
      why        — the chosen advisor's reason
      picked     — "Advisor A (llama3.2:3b): <judge's reason>"
      other      — the proposal not picked, one line, so the choice is visible
      problem    — set when no usable hypothesis came out, saying why
      steps      — every model call, for the expandable detail
    """
    view: dict[str, Any] = {"headline": summary_line(h), "why": (h or {}).get("why", ""),
                            "picked": "", "other": "", "problem": "", "steps": []}
    steps = (row or {}).get("steps") or []
    view["steps"] = [
        {"label": (f"Advisor {s['role']}" if s.get("step") == "advisor" else "Judge")
                  + f" — {s.get('model', '')}",
         "prompt": s.get("prompt", ""), "raw": s.get("raw") or "(no response)"}
        for s in steps
    ]
    if h:
        model = h.get("model", "")
        view["picked"] = (f"Advisor {h.get('picked', '?')}" + (f" ({model})" if model else "")
                          + f": {h.get('pick_reason', '')}")
        for s in steps:
            if s.get("step") == "advisor" and s.get("role") != h.get("picked") and s.get("parsed"):
                view["other"] = f"Advisor {s['role']} proposed: {summary_line(s['parsed'])}"
    elif row:
        view["problem"] = {
            "none_usable": "Neither advisor answered in the TEST/WHY/WATCH form — no hypothesis "
                           "this round. Their replies are under the steps below.",
            "no_response": "Neither advisor responded.",
        }.get(row.get("outcome", ""), "")
    return view
