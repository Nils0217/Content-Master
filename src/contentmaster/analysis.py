"""track -> ANALYSIS -> log -> improve.

The gap this closes: the old loop went straight from one post's raw
numbers to a single local model's "improve" suggestion — no comparison
against history, no check on whether the *previous* suggestion actually
correlated with anything moving. That's not analysis, it's one model
guessing from N=1.

This step queries warehouse/local.duckdb's `performance_history` (built by
`dbt run` in warehouse/ — see warehouse/README.md) for this
product/channel's real timeline, computes a trend and whether the last
improvement_note's suggestion looks like it helped, and — because sample
sizes here are still small enough that any of this could be noise —
prints the verdict for a human to confirm or override before downstream
steps (discuss.py) build on it. That's the human-in-the-loop half of this
step; discuss.py is the multi-model half.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import settings
from .human_loop import ReviewInterrupted

TREND_THRESHOLD = 0.05  # +/-5% vs historical average before calling it a trend, not noise

# 2026-09-15 (docs/SCHEDULE.md Phase 9, /grill-me design session): each
# checkpoint tier asks a different, appropriately-scoped question instead
# of judging overall success/failure too early. Comparisons stay *within*
# one tier (see _query_history's `checkpoint` filter) — a 24h reading is
# never compared against a 7d/30d baseline, that would be comparing
# different things. Only "24h" has real data flowing through it as of
# this build; "7d"/"30d" are defined now so nothing needs renaming later.
CHECKPOINT_QUESTIONS: dict[str, str] = {
    "24h": "did the algorithm actually push this post out at all, compared to this "
           "product/channel's own other 24h readings",
    "7d": "is the early heat sustained, compared to this product/channel's other 7-day readings",
    "30d": "long-term/time-series performance across everything published this month",
}


@dataclass
class AnalysisResult:
    product: str
    channel: str
    checkpoint: str
    n_prior_posts: int
    historical_avg_score: float | None
    current_score: float
    trend: str  # no_warehouse | no_history | insufficient_for_trend | improving | declining | flat
    prior_suggestion: str
    prior_suggestion_effectiveness: str  # unknown | looks_effective | looks_ineffective
    evidence: str
    human_confirmed: bool | None = None
    human_note: str = ""
    # 2026-09-14 (docs/SCHEDULE.md Phase 1/9 — external cross-validation):
    # both populated from warehouse/seeds/ Google Trends data, empty string
    # if the warehouse doesn't have it (never blocks the pipeline).
    external_signal: str = ""
    trending_context: str = ""
    # 2026-09-24: whether there is enough history here to say anything
    # comparative at all, using the same floor as the win/lose gate
    # (scoring.MIN_BASELINE_POSTS / MIN_BASELINE_EVENTS). Before this, the
    # two disagreed in the same printout: the gate correctly said "no
    # usable baseline" while this module reported a confident
    # "delta=-1.0000 ... does not support it" computed from one prior
    # post. The gate had been added *beside* the older analysis rather
    # than in front of it, so its conclusion never reached the thing
    # producing trends and recommendations.
    #
    # False does NOT mean produce nothing. It means produce a different
    # kind of thing — a hypothesis to test rather than a verdict on
    # numbers — which is what docs/SCHEDULE.md Phase 10 specified for a
    # failed gate 1 all along. Staying silent for the first eight posts
    # would switch the system off exactly during the period it has to
    # operate.
    has_baseline: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "product": self.product,
            "channel": self.channel,
            "checkpoint": self.checkpoint,
            "n_prior_posts": self.n_prior_posts,
            "historical_avg_score": self.historical_avg_score,
            "current_score": self.current_score,
            "trend": self.trend,
            "prior_suggestion": self.prior_suggestion,
            "prior_suggestion_effectiveness": self.prior_suggestion_effectiveness,
            "evidence": self.evidence,
            "human_confirmed": self.human_confirmed,
            "human_note": self.human_note,
            "external_signal": self.external_signal,
            "trending_context": self.trending_context,
            "has_baseline": self.has_baseline,
        }


def _query_history(product_name: str, channel: str, checkpoint: str) -> list[dict[str, Any]] | None:
    """Reads warehouse/local.duckdb's performance_history, scoped to one
    checkpoint tier — a 24h reading only gets compared against other 24h
    readings, never against 7d/30d ones (see CHECKPOINT_QUESTIONS above).
    Returns None if the warehouse hasn't been built yet, so the caller can
    fall back cleanly instead of crashing the pipeline on a missing file.

    The four raw counts are selected so the caller can derive
    `engagement_events` the same way scoring.judge() does (2026-09-27):
    this used to select only `engagement_score`, while the caller read a
    key named `engagement_events` that no row here has ever carried — not
    in this SELECT, not in performance_history, not in
    stg_success_history.sql. `.get()` returned None for every row, so the
    event count summed to 0 forever and `has_baseline` could never become
    True: `trend` was permanently pinned to "insufficient_for_trend" no
    matter how much real history accumulated. Deriving it from the counts
    rather than adding a mart column keeps one definition of an
    engagement event, in scoring.py, for both the gate and the trend.
    """
    db_path = settings.project_root / "warehouse" / "local.duckdb"
    if not db_path.exists():
        return None
    try:
        import duckdb

        from .scoring import engagement_events

        con = duckdb.connect(str(db_path), read_only=True)
        try:
            rows = con.execute(
                "select ts, engagement_score, improvement_note, evidence_used, "
                "external_cat_search_interest, like_count, repost_count, "
                "reply_count, bookmark_count "
                "from performance_history "
                "where product = ? and channel = ? and status = 'published' "
                "and checkpoint = ? order by ts",
                [product_name, channel, checkpoint],
            ).fetchall()
        finally:
            con.close()
        return [
            {"ts": r[0], "engagement_score": r[1], "improvement_note": r[2],
             "evidence_used": r[3], "external_interest": r[4],
             "engagement_events": engagement_events(
                 {"like_count": r[5], "repost_count": r[6],
                  "reply_count": r[7], "bookmark_count": r[8]}
             )}
            for r in rows
        ]
    except Exception:  # noqa: BLE001 — analysis degrading gracefully beats crashing the pipeline
        return None


def _query_external_signal(history: list[dict[str, Any]]) -> str:
    """Phase 1/9's 'cross validate against other data' half: does this
    product/channel's engagement score look any different on days with higher external
    (Google Trends "cat") search interest? Sample sizes here are tiny, so
    this stays descriptive, not a real statistical claim — it's context
    for a human/LLM to weigh, not a verdict.
    """
    with_interest = [h for h in history if h.get("external_interest") is not None]
    if not with_interest:
        return ""
    if len(with_interest) < 4:
        latest = with_interest[-1]["external_interest"]
        return (
            f"External signal: today's Google Trends 'cat' search interest is {latest} "
            f"(0-100 scale). Only {len(with_interest)} published post(s) have this data "
            "so far — not enough to say whether it correlates with engagement yet."
        )
    avg_interest = sum(h["external_interest"] for h in with_interest) / len(with_interest)
    high = [h for h in with_interest if h["external_interest"] > avg_interest]
    low = [h for h in with_interest if h["external_interest"] <= avg_interest]
    if not high or not low:
        return (
            f"External signal: search interest has been flat around {avg_interest:.0f} across "
            f"all {len(with_interest)} published posts — no high/low split to compare yet."
        )
    avg_score_high = sum(h["engagement_score"] or 0.0 for h in high) / len(high)
    avg_score_low = sum(h["engagement_score"] or 0.0 for h in low) / len(low)
    direction = "higher" if avg_score_high > avg_score_low else "lower"
    return (
        f"External signal: posts on above-average search-interest days (avg score "
        f"{avg_score_high:.4f}, n={len(high)}) look {direction} than below-average days "
        f"(avg score {avg_score_low:.4f}, n={len(low)}) — small n, treat as a hint, not proof."
    )


def _query_trending_context() -> str:
    """Pulls the 3 Google Trends 'related queries' reference tables (see
    warehouse/models/staging/stg_google_trends_related_top.sql,
    ..._related_rising.sql, ..._region.sql) into a short block of text —
    not for the engagement cross-validation above, but as grounding for
    discuss.py's strategy proposals ("what's actually trending around this
    topic right now"). Empty string (never raises) if the warehouse or
    those tables aren't there.

    2026-09-19 (code scan): took `product_name`/`channel` arguments that
    the body never used — these three tables are general Google Trends
    reference data, deliberately not scoped to one product (the docstring
    above already says so). Dropped rather than left as a false promise
    that the result is product-specific.
    """
    db_path = settings.project_root / "warehouse" / "local.duckdb"
    if not db_path.exists():
        return ""
    try:
        import duckdb

        con = duckdb.connect(str(db_path), read_only=True)
        try:
            top = con.execute(
                "select query from stg_google_trends_related_top order by score desc limit 8"
            ).fetchall()
            rising = con.execute(
                "select query, is_breakout, growth_pct from stg_google_trends_related_rising "
                "order by is_breakout desc, growth_pct desc nulls first limit 5"
            ).fetchall()
            region = con.execute(
                "select country from stg_google_trends_region order by relative_interest desc limit 3"
            ).fetchall()
            as_of = con.execute("select max(day) from stg_google_trends_timeline").fetchone()
        finally:
            con.close()
    except Exception:  # noqa: BLE001 — this is optional flavor, never worth crashing over
        return ""

    if not top and not rising and not region:
        return ""

    parts = []
    if top:
        parts.append("top related searches: " + ", ".join(r[0] for r in top))
    if rising:
        rising_bits = [
            f"{r[0]} ({'breakout' if r[1] else f'+{r[2]}%'})" for r in rising
        ]
        parts.append("rising related searches: " + ", ".join(rising_bits))
    if region:
        parts.append("highest-interest regions: " + ", ".join(r[0] for r in region))
    # 2026-09-24: the capture date, which the drafting prompt has stated
    # since the trend rewrite and this side did not. Without it the
    # advisors read a ten-day-old breakout as a live opportunity, and in a
    # real session recommended building a post around "cat in the hat
    # trend" on that basis. The seeds are hand-exported CSV
    # (warehouse/seeds/); automating the refresh is scheduled, and until
    # then saying how old the data is lets the reader discount it.
    captured = str(as_of[0])[:10] if as_of and as_of[0] else "an unknown date"
    return (f"Trending context (Google Trends, captured {captured}, not specific to this "
            f"product's own posts — steady searches age well, a breakout does not): "
            + "; ".join(parts))


def summarize_prior_suggestion(text: str, limit: int = 160) -> str:
    """One line for a suggestion that is really two advisors' full essays.

    Below both review surfaces on purpose. The first version of this lived
    inside _print_analysis(), so the terminal got a readable line while
    the Streamlit page — the default surface — kept dumping several
    hundred words verbatim, twice on the same screen. See
    .claude/skills/two-review-surfaces.
    """
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    return f"{flat[:limit]}... ({len(text)} chars in full)"


def describe_effectiveness(effectiveness: str) -> str:
    """Plain words for `unknown | looks_effective | looks_ineffective`.

    The raw value reads badly next to a label: the terminal printed
    "looks: looks_ineffective" and Streamlit "— looks unknown". More
    importantly `unknown` on its own sounds like missing data, when it is
    a real finding — usually that the advice was never followed, so there
    is nothing to judge.
    """
    return {
        "unknown": "not assessed — either the post did not say it followed the "
                   "advice, or there is no baseline to judge against",
        "looks_effective": "followed, and the score went up",
        "looks_ineffective": "followed, and the score did not go up",
    }.get(effectiveness, effectiveness)


def _judge_prior_suggestion(suggestion: str, followed: str, current_score: float,
                            last_score: float, has_baseline: bool) -> str:
    """Did last round's advice work? Usually the honest answer is that we
    cannot tell.

    2026-09-24. This used to be `current_score > last_score` and nothing
    else, which judged whether a suggestion worked purely from what
    happened afterwards — post hoc ergo propter hoc, written into the
    codebase. A real case: the suggestion was to write about litter-box
    hygiene, the post that went out was "Introducing Fluffy Roommate: The
    Ultimate Companion", and the suggestion was recorded as
    `looks_ineffective`. It had never been tested. That verdict then went
    into history, came back in the next prompt, and marked an untried
    direction as dead.

    Two things now have to hold before a verdict is given at all:

    * the post has to say it followed the advice. `evidence_used` is
      written by the drafting model for exactly this reason — it records
      what the draft actually acted on — and until now nothing read it.
    * there has to be a baseline. One post beating one other post is not
      evidence that advice worked.

    Either missing gives "unknown", which is not a gap in the data. It is
    the correct answer.
    """
    if not suggestion:
        return "unknown"
    if not (followed or "").strip() or followed.strip().lower() in ("none", "n/a", "-"):
        return "unknown"
    if not has_baseline:
        return "unknown"
    return "looks_effective" if current_score > last_score else "looks_ineffective"


def analyze_performance(
    product_name: str,
    channel: str,
    current_score: float,
    checkpoint: str = "24h",
) -> AnalysisResult:
    """Cross-validates this run's result against this product/channel's
    real history in the warehouse — not a single model guessing from one
    post's raw numbers. Pure computation, no human interaction here
    anymore (2026-09-16 — see confirm_with_human() below for why that
    moved out of this function): the caller runs discuss.py's
    synthesize_strategy() on this result first, *then* shows both the
    analysis and the recommendation together for one combined human
    review — confirming raw numbers before there's even a recommendation
    to react to didn't make sense. See pipeline._pull_and_analyze().

    `checkpoint` (2026-09-15): which time-horizon question this call is
    answering — see CHECKPOINT_QUESTIONS. All comparisons below stay
    within this one tier.
    """
    question = CHECKPOINT_QUESTIONS.get(checkpoint, checkpoint)
    history = _query_history(product_name, channel, checkpoint)

    if history is None:
        result = AnalysisResult(
            product=product_name, channel=channel, checkpoint=checkpoint, n_prior_posts=0,
            historical_avg_score=None, current_score=current_score, trend="no_warehouse",
            prior_suggestion="", prior_suggestion_effectiveness="unknown",
            evidence="warehouse/local.duckdb not found — run `dbt run` in warehouse/ to enable "
                     "real cross-validation. Proceeding with no history.",
        )
    elif not history:
        result = AnalysisResult(
            product=product_name, channel=channel, checkpoint=checkpoint, n_prior_posts=0,
            historical_avg_score=None, current_score=current_score, trend="no_history",
            prior_suggestion="", prior_suggestion_effectiveness="unknown",
            evidence=f"No prior published posts with a completed {checkpoint} checkpoint for this "
                     "product/channel — this is the first data point at this tier, nothing to "
                     "cross-validate against yet.",
        )
    else:
        from .scoring import MIN_BASELINE_EVENTS, MIN_BASELINE_POSTS

        n = len(history)
        avg_score = sum(h["engagement_score"] or 0.0 for h in history) / n
        delta = current_score - avg_score
        # Same floor the win/lose gate uses, read from the same constants
        # so the two can never drift into disagreeing about whether there
        # is anything to compare against.
        events = sum(int(h.get("engagement_events") or 0) for h in history)
        has_baseline = n >= MIN_BASELINE_POSTS and events >= MIN_BASELINE_EVENTS

        if not has_baseline:
            trend = "insufficient_for_trend"
        elif avg_score == 0:
            trend = "improving" if current_score > 0 else "flat"
        elif delta > avg_score * TREND_THRESHOLD:
            trend = "improving"
        elif delta < -avg_score * TREND_THRESHOLD:
            trend = "declining"
        else:
            trend = "flat"

        last = history[-1]
        prior_suggestion = last.get("improvement_note") or ""
        last_score = last["engagement_score"] or 0.0
        effectiveness = _judge_prior_suggestion(
            prior_suggestion, followed=last.get("evidence_used") or "",
            current_score=current_score, last_score=last_score, has_baseline=has_baseline,
        )

        if has_baseline:
            evidence = (
                f"[{checkpoint} checkpoint — {question}] "
                f"{n} prior post(s) at this tier carrying {events} engagement event(s); "
                f"historical avg score={avg_score:.4f}, current score={current_score:.4f} "
                f"(delta={delta:+.4f})."
            )
        else:
            # Raw numbers only. A delta against one prior post is a
            # difference, not a trend, and printing it as one is what made
            # this module contradict the gate in the same breath.
            evidence = (
                f"[{checkpoint} checkpoint — {question}] No usable baseline yet: "
                f"{n} comparable post(s) (need {MIN_BASELINE_POSTS}) carrying {events} "
                f"engagement event(s) (need {MIN_BASELINE_EVENTS}). This post scored "
                f"{current_score:.4f}. Nothing is being concluded from that — there is nothing "
                "to compare it against."
            )
        # The full note goes into the record; the on-screen copy is
        # shortened by _print_analysis.
        if prior_suggestion and effectiveness != "unknown":
            support = "supports" if effectiveness == "looks_effective" else "does not support"
            evidence += (
                f" The previous round's suggestion was acted on, and the score went from "
                f"{last_score:.4f} to {current_score:.4f}, which {support} it."
            )
        elif prior_suggestion:
            evidence += (
                " The previous round made a suggestion; whether this post actually followed it "
                "is not established, so it is not being scored either way."
            )

        result = AnalysisResult(
            product=product_name, channel=channel, checkpoint=checkpoint, n_prior_posts=n,
            historical_avg_score=avg_score, current_score=current_score, trend=trend,
            prior_suggestion=prior_suggestion, prior_suggestion_effectiveness=effectiveness,
            has_baseline=has_baseline,
            evidence=evidence,
        )

    result.external_signal = _query_external_signal(history or [])
    result.trending_context = _query_trending_context()
    return result


def confirm_with_human(result: AnalysisResult, recommendation: str) -> AnalysisResult:
    """2026-09-16 — replaces the old `_confirm_with_human()` that ran
    *inside* analyze_performance(), before discuss.py's recommendation
    even existed. Now called from pipeline.py after both analyze_performance()
    and synthesize_strategy() have run, so the human reviews metrics +
    the actual recommendation together, one combined decision — not a
    confirmation on raw numbers in a vacuum.

    If the human disagrees, `result.human_note` stores their feedback
    *alongside* `recommendation` (the LLM's take) — neither overwrites
    the other. Both get read back for the next draft: see
    modiqo_play.find_best_prior()'s `human_feedback` field and
    draft_generator.py's prompt, which includes both and tells the model
    to prioritize the human's direction where they conflict.
    """
    print("\n" + "=" * 60)
    print(f"ANALYSIS  {result.product} · {result.channel} · checkpoint={result.checkpoint}")
    print("-" * 60)
    print(f"prior published posts : {result.n_prior_posts}")
    if result.historical_avg_score is not None:
        print(f"historical avg score   : {result.historical_avg_score:.4f}")
    print(f"current score          : {result.current_score:.4f}")
    print(f"trend                  : {result.trend}")
    if result.prior_suggestion:
        # Truncated on purpose: the full note is two advisors' complete
        # text, and printing it here as well as inside `evidence` put the
        # same several hundred words on screen three times in one report.
        # The whole thing is still in the record.
        print(f"prior suggestion       : {summarize_prior_suggestion(result.prior_suggestion)}")
        print(f"was it followed        : {describe_effectiveness(result.prior_suggestion_effectiveness)}")
    print(result.evidence)
    if result.external_signal:
        print(result.external_signal)
    if result.trending_context:
        print(result.trending_context)
    print("-" * 60)
    print(f"RECOMMENDATION for the next post: {recommendation}")
    print("=" * 60)
    # 2026-09-19 (code scan): every other prompt in this project rejects
    # anything it does not recognise rather than silently defaulting —
    # see topic_index._review_topics_interactive(), which was changed to
    # do exactly that after a real run where a user's input was quietly
    # treated as "accept all". This one still defaulted blank-or-anything
    # to Yes, i.e. a stray keypress silently recorded the human as having
    # confirmed an analysis they never read. Now an explicit y/n only.
    while True:
        try:
            choice = input("Does this look right? [y]es / [n]o, add your own feedback > ").strip().lower()
        except (EOFError, KeyboardInterrupt) as e:
            raise ReviewInterrupted("Analysis review interrupted (stdin closed or Ctrl+C)") from e
        if choice in ("y", "n"):
            break
        print(f"{choice!r} isn't a valid choice — enter exactly 'y' or 'n'.")

    if choice == "n":
        # 2026-09-19 (code scan): this was the single input() in the whole
        # project with no EOFError/KeyboardInterrupt handling — Ctrl+C
        # here produced a raw traceback instead of the clean
        # ReviewInterrupted every other prompt raises.
        try:
            note = input(
                "What should the next post do instead? (kept *alongside* the LLM's "
                "recommendation above, not replacing it — both get read next time): "
            ).strip()
        except (EOFError, KeyboardInterrupt) as e:
            raise ReviewInterrupted("Analysis review interrupted (stdin closed or Ctrl+C)") from e
        result.human_confirmed = False
        result.human_note = note
    else:
        result.human_confirmed = True
    return result
