"""Engagement score and the win/lose gates — docs/SCHEDULE.md Phase 10.

Everything tunable about "did this post do well?" lives here, in one
place, deliberately: none of these constants can be honestly calibrated
yet (the account they were designed against had 5 real likes across 12
posts), so the point is to be able to re-run stored history against new
numbers later instead of having to re-collect it. That only works if the
raw counts are stored alongside the derived score — see
pipeline._reading_from_counts().

What this replaces
------------------
`ctr = weighted / max(total, 1)` (pipeline.py) — a weighted *average*.
It could not tell 1 like from 5: a likes-only post scored exactly 1.0
however many likes it had, and a zero-engagement post scored 0. The real
distribution over 12 posts was [1,1,1,1,1,0,0,0,0,0,0,0], so
`SUCCESS_CTR_THRESHOLD = 0.5` passed 5 of 12 (42%) on an account that had
never been reposted or replied to even once. The threshold was not
measuring anything, and no percentile laid on top of that score could
have helped — p75 of it is 1.0, which is also its maximum.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

# Weight 3 = the reader spent something real: a public endorsement, or a
# private "I want this later". Weight 1 = near-free at this account size
# (a like), or ambiguous in sign (a reply can be hostile — reply *value*
# is carried by reply-content analysis, not by this number).
# Quote posts are captured but deliberately not scored: a quote can be a dunk.
ENGAGEMENT_WEIGHTS: dict[str, float] = {
    "repost_count": 3.0,
    "bookmark_count": 3.0,
    "like_count": 1.0,
    "reply_count": 1.0,
}
# Counted for gate 1's "is there anything here at all" test. Same keys as
# the weights — quote_count is excluded from both on purpose.
EVENT_KEYS = tuple(ENGAGEMENT_WEIGHTS)
# The two that make gate 2's quality condition.
HIGH_SIGNAL_KEYS = ("repost_count", "bookmark_count")

MIN_BASELINE_POSTS = 8      # provisional — see the module docstring
MIN_BASELINE_EVENTS = 10    # so "8 posts that are all zero" is not a baseline
BASELINE_PERCENTILE = 75
TENTATIVE_BELOW_N = 20      # proven stays `_tentative` while 8 <= n < 20

# The share of every test group that must deliberately go AGAINST the
# current best-known evidence.
#
# 2026-09-21, /grill-me. The problem it answers: the whole design hands
# the LLM evidence rather than instructions ("this post got 1 like, 0
# reposts — extend it, improve it, or disagree with reasons"). But an LLM
# shown "posts with links scored 40% lower" simply stops using links.
# That is obedience, not weighing, and rewording an instruction as a
# statistic does not change it. Nothing in the prompt can fix this,
# because the problem is what the model does with the prompt.
#
# So the control arm is structural instead: a fixed share of every group
# is required to violate whatever the current best evidence says. If the
# evidence still holds, those posts do worse and it is confirmed. If they
# do not do worse, the evidence has expired — which is the thing a system
# that only ever exploits its best-known pattern can never find out.
# docs/SCHEDULE.md Phase 10 already named this risk: "a single fixed
# winning pattern will eventually stop working, and the system has to be
# able to notice that about itself."
#
# Derived, never written down as a count: change MIN_BASELINE_POSTS or
# this ratio and the number of control posts follows automatically.
CONTROL_ARM_RATIO = 0.20


def control_arm_size(group_size: int = MIN_BASELINE_POSTS) -> int:
    """How many posts in a group must contradict the current evidence.

    Rounded UP, because CONTROL_ARM_RATIO is a floor, not a target: at a
    group of 12, rounding down would give 2 controls — 16.7%, under the
    20% the rule promises. Ceiling also guarantees at least one control
    for any non-empty group, so the rule can never quietly switch itself
    off at small sizes.
    """
    if group_size <= 0:
        return 0
    return -(-int(round(group_size * CONTROL_ARM_RATIO * 1000)) // 1000)


@dataclass
class Verdict:
    state: str
    reason: str
    score: float
    baseline_n: int
    baseline_p75: float | None

    @property
    def is_win(self) -> bool:
        return self.state in ("proven", "proven_tentative")


def engagement_score(counts: dict[str, Any]) -> float:
    """Weighted SUM, not an average — so more engagement always scores
    strictly higher, which is the property the old formula lacked.
    """
    return float(sum(w * (counts.get(k) or 0) for k, w in ENGAGEMENT_WEIGHTS.items()))


def score_of(metrics: dict[str, Any]) -> float:
    """The engagement score of a stored metrics row, recomputed from its
    raw counts when the stored score is missing.

    2026-09-21: a row written while an older field shape was in flight can
    carry raw counts but no `engagement_score` — a queue entry captured
    before a rename and finalised after it does exactly that. Reading the
    field with a plain `.get(..., 0)` then silently reports zero
    engagement for a post that really got some, which is how a prompt came
    to tell the LLM "1 like ... engagement score 0" in the same sentence,
    and why comparable_history() skipped the row entirely.

    Recomputing is always safe: the raw counts are the source of truth and
    the score is a pure function of them. A row with neither is genuinely
    unmeasured and scores 0, which is correct.
    """
    stored = metrics.get("engagement_score")
    if stored is not None:
        return float(stored)
    return engagement_score(metrics)


def engagement_events(counts: dict[str, Any]) -> int:
    """Unweighted count of real interactions — gate 1's second condition."""
    return sum(int(counts.get(k) or 0) for k in EVENT_KEYS)


def percentile(values: list[float], pct: int) -> float:
    """Nearest-rank percentile. Chosen over interpolation on purpose:
    with n around 8 the interpolated value is a number no actual post
    ever scored, which makes "your score beat the baseline" impossible to
    explain to the human who has to trust it.
    """
    if not values:
        raise ValueError("percentile of an empty baseline")
    ordered = sorted(values)
    rank = max(1, -(-pct * len(ordered) // 100))  # ceil, 1-indexed
    return ordered[min(rank, len(ordered)) - 1]


def judge(counts: dict[str, Any], baseline: Iterable[dict[str, Any]]) -> Verdict:
    """`baseline` is the comparable prior posts' raw count dicts — already
    filtered by caller to the same channel and checkpoint tier (and, once
    the column exists, the same account era and nothing else).

    Confidence is carried by the state label rather than by moving the
    threshold, so having 8/10/75 wrong costs a conservative label instead
    of a false positive.
    """
    score = engagement_score(counts)
    rows = list(baseline)
    scores = [score_of(r) for r in rows]
    total_events = sum(engagement_events(r) for r in rows)

    # Gate 1 — is there a usable baseline at all?
    if len(rows) < MIN_BASELINE_POSTS or total_events < MIN_BASELINE_EVENTS:
        return Verdict(
            state="hypothesis",
            reason=(
                f"No usable baseline yet: {len(rows)} comparable post(s) "
                f"(need {MIN_BASELINE_POSTS}) carrying {total_events} engagement event(s) "
                f"(need {MIN_BASELINE_EVENTS}). No win/lose judgement is being made — "
                "this round produces a falsifiable hypothesis instead."
            ),
            score=score, baseline_n=len(rows), baseline_p75=None,
        )

    p75 = percentile(scores, BASELINE_PERCENTILE)
    high_signal = sum(int(counts.get(k) or 0) for k in HIGH_SIGNAL_KEYS)

    # Gate 2, quality half. Also covers the degenerate case where p75
    # collapses to 0 (most posts with no engagement at all), which would
    # otherwise let "1 like" win.
    if high_signal < 1:
        return Verdict(
            state="likes_only",
            reason=(
                f"score {score:g} vs baseline p{BASELINE_PERCENTILE} {p75:g} over {len(rows)} "
                f"post(s), but no repost and no bookmark — nobody spent anything on it."
            ),
            score=score, baseline_n=len(rows), baseline_p75=p75,
        )

    # Gate 2, volume half.
    if score <= p75:
        return Verdict(
            state="below_baseline",
            reason=(
                f"score {score:g} does not beat baseline p{BASELINE_PERCENTILE} {p75:g} "
                f"over {len(rows)} comparable post(s)."
            ),
            score=score, baseline_n=len(rows), baseline_p75=p75,
        )

    tentative = len(rows) < TENTATIVE_BELOW_N
    return Verdict(
        state="proven_tentative" if tentative else "proven",
        reason=(
            f"score {score:g} beats baseline p{BASELINE_PERCENTILE} {p75:g} over {len(rows)} "
            f"post(s), with {high_signal} repost/bookmark."
            + (f" Labeled tentative while the baseline is under {TENTATIVE_BELOW_N} posts."
               if tentative else "")
        ),
        score=score, baseline_n=len(rows), baseline_p75=p75,
    )
