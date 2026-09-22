"""Orchestrates the full loop (see docs/WHITEPAPER.md §2):

  Cognee -> generate drafts + images (local Ollama, Cloudflare Workers AI)
    -> [human review, in a browser by default, or the terminal with
       --terminal] -> publish -> queue for review (plays/_tracking_review.jsonl)
    ... real time passes ...
    -> `contentmaster review`: checkpoint (24h/7d/30d) -> pull real metrics
    -> analysis (cross-validated vs. warehouse history, checkpoint-scoped)
    -> discuss (2 local models propose; a 3rd-model judge exists but is
    off by default, see discuss.py's _JUDGE_ENABLED) -> [human confirms
    or disagrees, in a browser by default, or the terminal with
    --terminal] -> log (Modiqo, success AND failure both feed forward
    now) -> next run's generation reads back the highest-maturity
    completed analysis available

2026-09-17 (Phase 5 design, docs/LOG.md): review moved out of `run()`'s
own terminal by default. Drafts (and their eagerly generated images) get
queued (plays/_draft_review.jsonl) and shown in a browser
(streamlit_app.py), launched automatically. `--terminal` keeps the
original inline text-then-image flow with no browser at all, for
whoever prefers it.

2026-09-18 (Phase 5 extended to `contentmaster review`, /grill-me design
session, docs/LOG.md): the same treatment for analysis.confirm_with_human(),
which had the same blocking-input() problem. Each due post's metrics pull
+ analysis + recommendation runs eagerly, then either confirms right in
the terminal (--terminal) or gets queued (plays/_analysis_review.jsonl)
and shown in the same browser page's Analysis review tab, with
Modiqo's capture (success/failure, decided by scoring.py's gates,
independent of the human's confirm/disagree choice) deferred until that
decision actually happens — see apply_analysis_decision().

2026-09-15 (docs/SCHEDULE.md Phase 9, docs/LOG.md — /grill-me design
session): publish used to be immediately followed by a metrics pull and a
full analyze+discuss+capture cycle, seconds after the post went live —
that's noise, not signal (a real example: a post read 0 engagement). `run()`
now stops at publish + queuing; the analysis half moved to a separate,
later-triggered step (`run_review()`) that only looks at a post once
real time — one of 3 checkpoints — has actually passed.

HydraDB and RocketRide were removed entirely 2026-09-13 — draft
generation was never RocketRide's cloud service to begin with, only ever
local Ollama calls; see docs/LOG.md.

Run with: contentmaster run --whitepaper "Marketing hack white paper.pdf"
          contentmaster run --whitepaper "..." --terminal   (no browser)
Review:   contentmaster review
          contentmaster review --terminal                   (no browser)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import (
    analysis_queue,
    audit,
    document,
    draft_queue,
    image_generator,
    metrics_store,
    topic_index,
    tracking_review,
)
from .analysis import AnalysisResult, analyze_performance, confirm_with_human
from .cognee_client import CogneeClient
from .config import CogneeSettings, settings
from .discuss import synthesize_strategy
from .draft_generator import DraftGenerator
from .human_loop import ReviewInterrupted, review_draft, review_image
from .modiqo_play import capture_failure, capture_success, comparable_history, find_best_prior
from .platforms.base import (
    PlatformAPIError,
    PlatformConfigError,
    PlatformRateLimitError,
    PostNotFoundError,
)
from .platforms.registry import PLATFORMS, get_platform
from .product_registry import describe as describe_product, remember as remember_product
from .publish_failure import PublishFailure, classify as classify_failure
from .publish_guard import DraftNotPublishable, publish_blockers
from .scoring import engagement_score, judge
from .slug import slugify


# Last-resort stand-in for `--features`, used only when generation cannot
# be grounded in a topic index or the document text. These describe this
# pipeline itself (they are left over from when the demo whitepaper WAS
# this project), so they are wrong for any real product — see run().
PLACEHOLDER_FEATURES: tuple[str, ...] = (
    "compound memory",
    "muscle-memory replay",
    "human-in-the-loop safety",
)

# 2026-09-20: the engagement score, its weights and the win/lose gates all
# moved to scoring.py — see that module for what was wrong with the
# weighted average this replaced, and why every tunable number now lives
# in one place. `SUCCESS_CTR_THRESHOLD` is gone with it: an absolute
# threshold cannot work on a sum with no upper bound, and the percentile
# gate makes a win rare by construction at any account size, so it never
# needs re-tuning.


def _dataset_slug(product_name: str) -> str:
    """Each product gets its own Cognee dataset so runs for different
    products/whitepapers never cross-contaminate each other's knowledge
    graph — search() only ever sees what was ingested for *this* product.
    """
    return slugify(product_name, default="contentmaster")


def step1_cognee_extract_topics(whitepaper_path: str, product_name: str) -> list[dict[str, Any]]:
    """Replaces the old single-fixed-query extraction (2026-09-15,
    docs/SCHEDULE.md Phase 9 — see docs/LOG.md for the full /grill-me
    design session behind this). That version asked Cognee the exact same
    question every single run and handed the same blob of text to every
    draft regardless of how many times the pipeline had already run —
    which is *why* repeat runs kept writing near-identical posts, not a
    flaw in the LLM call itself.

    Now: `topic_index.sync_topics()` only calls Cognee (the `_extract`
    closure below — add/cognify/search) when the whitepaper's content has
    actually changed since last time (a cheap local file hash, no Cognee
    involved in that check itself); otherwise it returns the cached index
    untouched, zero Cognee calls. Returns the full topic list — step3
    picks which ones to actually write about.
    """
    def _extract() -> str:
        cognee = CogneeClient(CogneeSettings(dataset=_dataset_slug(product_name)))
        audit.log_event("cognee", "add.start", file=whitepaper_path)
        cognee.add_document(whitepaper_path, labels="whitepaper")
        audit.log_event("cognee", "add.done")

        audit.log_event("cognee", "cognify.start")
        cognee.cognify()
        audit.log_event("cognee", "cognify.done")

        query = (
            "List the distinct topics, features, benefits, or claims described in this "
            "document. For each one, respond on its own line in exactly this format: "
            "Topic: <short name> | Brief: <one or two sentence summary>."
        )
        audit.log_event("cognee", "search.start", query="topic list")
        result = cognee.search(query)
        audit.log_event("cognee", "search.done")

        # result shape: [{"search_result": ["free text..."], ...}, ...]
        for entry in result or []:
            for text in entry.get("search_result", []):
                if isinstance(text, str) and text.strip():
                    return text.strip()
        return ""

    index = topic_index.sync_topics(product_name, whitepaper_path, _extract)
    return index.get("topics") or []


def step3_generate_drafts(
    product_name: str, features: list[str], channel: str, topics_all: list[dict[str, Any]], n: int = 1,
    target: dict[str, Any] | None = None, context: str = "",
) -> list[dict[str, Any]]:
    """Generates drafts via the local LLM (see draft_generator.py). When
    Modiqo already has a play for this (product, channel) — i.e. a prior
    run's winning post + its real metrics + the LLM's improvement note —
    that gets fed back in as `prior` so this run writes a genuinely
    improved version instead of a cold-start draft. This is the "read
    results -> analysis -> new post -> another loop" cycle, not a plain
    replay: every run calls the LLM again on purpose, trading the old
    "run #2 is free" shortcut for content that actually compounds.

    `topics_all` (the product's full topic index, see
    step1_cognee_extract_topics/topic_index.py) is narrowed here to the
    `n` currently-least-used topics — real variety across runs, not a
    hardcoded per-draft template.

    `context` (2026-09-19, code scan) is the grounding text used when the
    topic index is empty — Cognee down, or nothing extracted for this
    product yet. This argument existed on draft_posts() all along but no
    caller ever passed it, which made draft_generator._llm_draft_posts()
    unreachable: a Cognee outage skipped the LLM completely and returned
    the fixed template string for every draft, identically, every run.
    See _extract_topics_and_generate() for where it comes from.
    """
    play = find_best_prior(product_name, channel)
    # 2026-09-21: every topic is offered, with its usage count, and the
    # model chooses. pick_topics() used to decide here — before the model
    # saw anything — and the choice was then imposed as an absolute
    # grounding rule, so no amount of prior evidence could ever change what
    # a post was about. pick_topics() still exists for the least-used
    # ordering; it is no longer the thing that decides.
    offered = topic_index.pick_topics({"topics": topics_all}, len(topics_all)) or topics_all
    generator = DraftGenerator()
    audit.log_event("draft_generator", "draft.start", product=product_name, channel=channel,
                     topics_offered=[t["topic"] for t in offered], improving_on_prior=bool(play),
                     has_context=bool(context.strip()))
    drafts = generator.draft_posts({"name": product_name, "features": features}, channel, n=n,
                                    topics=offered, prior=play, target=target, context=context)
    # `sources` makes a template-only run visible in audit/events.jsonl.
    # Without it, "draft.done n=1" looked identical whether the LLM wrote
    # the post or the canned string did — which is exactly why the dead
    # context fallback above went unnoticed for so long.
    audit.log_event("draft_generator", "draft.done", n=len(drafts),
                     sources=sorted({d.get("source", "unknown") for d in drafts}),
                     topics_chosen=[d.get("topic") for d in drafts],
                     axes=[d.get("test_axis") for d in drafts if d.get("test_axis")],
                     went_against_evidence=[d.get("evidence_against") for d in drafts
                                             if (d.get("evidence_against") or "").lower()
                                             not in ("", "none")])
    return drafts


def step4_human_review(draft: dict[str, Any], product_name: str) -> Any:
    """`regenerate_fn` (see human_loop.review_draft) lets the reviewer type
    feedback instead of a literal replacement — see docs/LOG.md 2026-09-15
    for the real broken-post bug this closes (feedback typed into the old
    single [e]dit prompt went out published verbatim). Bound to `draft`'s
    own `topic`/`brief` when the draft came from the topic index (see
    draft_generator.py), so a revision stays grounded in the same fact —
    "" for the old context/fixed-template drafts, still functional either
    way since draft_generator.revise_post()'s `brief` is optional.
    """
    generator = DraftGenerator()

    def regenerate_fn(current_text: str, feedback: str) -> str | None:
        return generator.revise_post(product_name, current_text, feedback,
                                      brief=draft.get("brief", ""), channel=draft.get("channel", ""))

    decision = review_draft(draft, regenerate_fn=regenerate_fn)
    audit.log_event("human_loop", "reviewed", draft_id=draft["id"],
                     approved=decision.approved, edited=decision.edited,
                     note=decision.reviewer_note)
    return decision


def step4b_generate_image(draft: dict[str, Any], final_text: str) -> tuple[bytes | None, str]:
    """Phase 4, 2026-09-16 (see image_generator.py) — runs right after
    text approval, before publish, so the image prompt is grounded in the
    *final* approved text, not a draft that might still change. Silently
    returns (None, "") if Cloudflare isn't configured or generation fails
    — text-only publish, same as before this feature existed. If an image
    *is* generated, it still goes through review_image() (a human looks
    at it before it can be attached — same brand-safety principle as
    review_draft(), see human_loop.py) before this returns.
    """
    prompt = draft.get("brief") or final_text
    image_bytes = image_generator.generate_image(prompt)
    if image_bytes is None:
        return None, ""

    def regenerate_fn(p: str) -> bytes | None:
        return image_generator.generate_image(p)

    approved = review_image(image_bytes, prompt, regenerate_fn=regenerate_fn, draft_id=draft["id"])
    if approved is None:
        audit.log_event("image_generator", "skipped", draft_id=draft["id"])
        return None, ""
    audit.log_event("image_generator", "attached", draft_id=draft["id"])
    return approved, prompt[:200]


def step5_publish(
    draft: dict[str, Any], final_text: str, image: bytes | None = None, image_alt: str = "",
    original_text: str | None = None, acknowledged: bool = False,
) -> dict[str, Any]:
    """Posts for real on any *implemented* platform (see
    platforms/registry.py's PLATFORMS) — gated on the human approval that
    already happened in step4 (human_loop.review_draft), this project's
    "explicit per-post approval" checkpoint. A channel with no implemented
    adapter yet (see PLANNED_PLATFORMS), or one whose publish call errors,
    stays a simulated, clearly-labeled stand-in.

    A publish failure never raises out of here — every platform error
    degrades to the simulated stand-in with a printed warning. See the
    handlers below; before 2026-09-19 an auth/rate-limit failure escaped
    them and crashed the run.

    `image`/`image_alt` (2026-09-16, Phase 4): optional, already
    human-approved by step4b_generate_image() if present — platforms that
    don't support images yet just ignore them (see platforms/base.py).

    2026-09-20: raises PublishFailed *before* touching a platform
    when `final_text` does not look like a post at all (see
    publish_guard.py — a reviewer's instruction typed into an edit box
    once went out as a live post). The check lives here, at the single
    point every review surface already funnels through, rather than in
    any one UI: the previous fix lived in the terminal prompt and the
    Streamlit UI added three days later simply did not have it.
    Unlike a platform failure this does NOT degrade to a simulated
    publish — that is recorded as published, which would file the
    accident rather than stop it. `acknowledged=True` overrides, for a
    human who has seen the warning and means it.
    """
    hard = get_platform(draft["channel"]).constraints.violations(final_text) \
        if draft.get("channel") in PLATFORMS else []
    if hard or not acknowledged:
        # `acknowledged` lets a human insist on an unusual-looking post; it
        # cannot waive the platform's own rules, which are not a matter of
        # judgement.
        blockers = hard if hard else publish_blockers(
            final_text, original_text or draft.get("text"), channel=draft.get("channel"))
        if blockers:
            reason = ("Refusing to publish — this does not look like a post:\n  - "
                      + "\n  - ".join(blockers))
            failure = classify_failure(DraftNotPublishable(reason))
            audit.log_event("publish", "blocked", draft_id=draft.get("id"),
                             channel=draft.get("channel"), kind=failure.kind,
                             error_type=failure.error_type, reasons=blockers,
                             text_length=len(final_text or ""))
            print(f"[publish blocked: {failure.kind}] {reason}")
            # Raised as PublishFailed so every surface catches one type,
            # whether the rejection came from our own pre-flight check or
            # from the platform itself.
            raise PublishFailed(failure, draft.get("id", ""))
    channel = draft["channel"]
    if channel in PLATFORMS:
        try:
            platform = get_platform(channel)
            result = platform.publish_post(final_text, image=image, image_alt=image_alt)
            post_ref = result.get("uri") or result.get("id")
            published = {**draft, "text": final_text, "status": "published", "post_ref": post_ref}
            audit.log_event("publish", f"{channel}.posted", draft_id=draft["id"], ref=post_ref,
                             has_image=bool(image))
            return published
        except Exception as e:
            # 2026-09-21: every platform error used to be caught here and
            # answered with a simulated publish — recorded as published,
            # queued for measurement, and shown in the browser as
            # `Published.`. A real failure and an unimplemented platform
            # became indistinguishable, which is how a 322-character post
            # was reported as live while never reaching Bluesky at all.
            # Now the failure is classified, recorded with its full
            # reason, and raised.
            failure = classify_failure(e)
            audit.log_event("publish", "failed", draft_id=draft["id"], channel=channel,
                             kind=failure.kind, error_type=failure.error_type,
                             reason=failure.reason, text_length=len(final_text))
            print(f"[publish failed: {failure.kind}] {failure.explain()}")
            raise PublishFailed(failure, draft["id"]) from e

    # Only reachable for a channel with no adapter at all. This is the one
    # remaining meaning of "simulated": the platform was never attempted,
    # not that it was attempted and failed.
    published = {**draft, "text": final_text, "status": "published (simulated)"}
    audit.log_event("publish", "simulated", draft_id=draft["id"], channel=channel,
                     note="no adapter for this channel; nothing was sent")
    return published


def step6_queue_for_review(product_name: str, channel: str, published: dict[str, Any],
                            edited: bool, reviewer_note: str) -> None:
    """Replaces the old step6_track_metrics + step7_modiqo_capture pair
    (2026-09-15, docs/SCHEDULE.md Phase 9 — see the module docstring for
    why). No metrics pull here at all: writing this entry *is* the
    "confirmed it really published" record. The real analysis happens
    later, from `contentmaster review` (see run_review() below), once a
    checkpoint's worth of real time has actually passed.
    """
    try:
        tracking_review.queue_for_review(
            post_id=published["id"], product=product_name, channel=channel,
            post_ref=published.get("post_ref"), final_text=published["text"],
            edited=edited, reviewer_note=reviewer_note,
        )
    except tracking_review.NotPublishedForReal as e:
        # A simulated publish (channel with no adapter). Recorded plainly
        # rather than tracked as if it were online — and, since 2026-09-21,
        # a *failed* publish never gets here at all: step5_publish raises
        # instead of degrading, so "simulated" now means only what it says.
        audit.log_event("tracking_review", "not_tracked", post_id=published["id"],
                         product=product_name, channel=channel, reason=str(e))
        print(f"[not tracked] {e}")
        return
    audit.log_event("tracking_review", "queued", post_id=published["id"],
                     product=product_name, channel=channel)

    # 2026-09-15 (topic_index.py): usage counts only bump once a post
    # actually publishes — a draft rejected at human review never reaches
    # here, so its topic isn't penalized for a wording problem rather than
    # the topic itself (see docs/LOG.md for the full reasoning).
    topic = published.get("topic")
    if topic:
        topic_index.record_topic_used(product_name, topic)
        audit.log_event("topic_index", "used", product=product_name, topic=topic)


class PublishFailed(RuntimeError):
    """A real publish attempt failed. Nothing reached the platform.

    Carries the classification so a caller can tell an environment blip
    (send the same bytes again later) from a content rejection (needs a
    revision and another human decision) without re-inspecting the
    original exception.
    """

    def __init__(self, failure: PublishFailure, draft_id: str):
        super().__init__(failure.explain())
        self.failure = failure
        self.draft_id = draft_id


class PostWasDeleted(RuntimeError):
    """Raised out of _pull_checkpoint_metrics() when the post is gone, so
    the caller abandons this post instead of analysing invented numbers.
    """


def _reading_from_counts(channel: str, checkpoint: str, real: dict[str, Any]) -> dict[str, Any]:
    """The one place raw platform counts become a metrics row, shared by
    the checkpoint pull and `contentmaster refresh` so the two can never
    drift into writing differently-shaped rows for the same post.
    """
    raw = {k: real.get(k) or 0 for k in
           ("like_count", "repost_count", "reply_count", "bookmark_count", "quote_count")}
    return {
        "source": f"{channel} (live, {checkpoint})",
        "checkpoint": checkpoint,
        # 2026-09-21: `ctr`, `impressions`, `clicks` and `conversions` are
        # gone. They were hotdata.dev-era ad-tech names holding social
        # numbers: `clicks` was the like count, `conversions` the repost
        # count, and `impressions` was `max(total_engagement, 1)` — a
        # figure with no relationship to reach at all, since Bluesky
        # exposes no impressions. Keeping them meant the same number was
        # stored twice under two names once raw counts landed, which is a
        # standing invitation to update one and not the other. `ctr` in
        # particular read as a rate: the value 1.0 meant "1 point", not
        # "100%".
        "engagement_score": round(engagement_score(raw), 4),
        # Raw counts always, alongside the derived score: none of the
        # weights or gate constants can be honestly calibrated yet, so
        # history has to stay re-runnable against new ones (scoring.py).
        **raw,
    }


class MetricsUnavailable(RuntimeError):
    """A real reading could not be taken this run — network, rate limit,
    or the platform is not configured.

    Nothing is written and the checkpoint is NOT marked done, so the post
    stays due and the next run tries again. This used to be answered with
    metrics_store.mock_metrics() (now deleted): invented numbers, written
    in exactly the shape of a real reading. "Could not measure" and
    "measured zero" are completely different facts, and a baseline built
    from a mixture of the two is not a baseline.
    """


def _pull_checkpoint_metrics(entry: dict[str, Any], checkpoint: str) -> dict[str, Any]:
    """Real engagement pull for one tracked post at one checkpoint. Writes
    a row to metrics/post_metrics.jsonl only when a real reading was
    actually taken (warehouse/'s dbt project reads that file directly —
    see warehouse/models/staging/stg_post_metrics.sql).

    Raises PostWasDeleted if the platform says the post is gone, and
    MetricsUnavailable if it could not be reached at all. Neither writes
    anything: there is no longer any path in this codebase that invents a
    number to keep a row shape happy.
    """
    post_id, channel, post_ref = entry["post_id"], entry["channel"], entry.get("post_ref")
    reading = None
    if post_ref and channel in PLATFORMS:
        try:
            platform = get_platform(channel)
            real = platform.get_post_metrics(post_ref)
            reading = _reading_from_counts(channel, checkpoint, real)
            audit.log_event(channel, "metrics.pulled", checkpoint=checkpoint, **real)
        except PostNotFoundError as e:
            # Listed before PlatformAPIError on purpose — it is a subclass,
            # so the generic handler below would otherwise swallow it.
            tracking_review.mark_deleted(entry, detail=str(e))
            audit.log_event(channel, "post.deleted", post_id=post_id, checkpoint=checkpoint,
                             post_ref=post_ref, detected_by=f"{checkpoint} checkpoint")
            print(f"[deleted] {post_ref} no longer exists on {channel} — recorded as deleted, "
                  "no metrics written. It will not be polled or reviewed again.")
            raise PostWasDeleted(post_ref or post_id) from e
        except (PlatformConfigError, PlatformAPIError) as e:
            audit.log_event("metrics", "unavailable", post_id=post_id, channel=channel,
                             checkpoint=checkpoint, error_type=type(e).__name__, reason=str(e))
            print(f"[skipped] Could not read {channel} metrics for {post_id} at the {checkpoint} "
                  f"checkpoint ({e}). Nothing written; the checkpoint stays due and the next "
                  "run will try again.")
            raise MetricsUnavailable(str(e)) from e

    if reading is None:
        # No post_ref, or the channel has no adapter — this post is not
        # something that can be measured, and tracking_review should never
        # have accepted it (see queue_for_review's published_for_real gate).
        audit.log_event("metrics", "unmeasurable", post_id=post_id, channel=channel,
                         checkpoint=checkpoint, post_ref=post_ref)
        raise MetricsUnavailable(
            f"{post_id} has no usable post reference on {channel} — it cannot be measured."
        )
    metrics_store.write_metrics(post_id, channel, reading)
    metrics = metrics_store.get_metrics(post_id)
    audit.log_event("metrics", "recorded", post_id=post_id, checkpoint=checkpoint, metrics=metrics)
    return metrics


def _pull_and_analyze(entry: dict[str, Any], checkpoint: str) -> tuple[dict[str, Any], AnalysisResult, str]:
    """2026-09-18 (Phase 5 extended to `contentmaster review`, /grill-me
    design session, see docs/LOG.md) — the eager half of what used to be
    one synchronous step_checkpoint_analyze(): pull real (now-matured)
    metrics, cross-validate against history, get the multi-model
    recommendation. Shared by both the terminal path and the streamlit
    path (mirrors _extract_topics_and_generate() being shared by
    `run()`'s two paths) — no human interaction and no
    capture_success/capture_failure here, see _finalize_analysis() for
    what happens with the result once a human has actually looked at it.
    """
    product_name, channel = entry["product"], entry["channel"]
    metrics = _pull_checkpoint_metrics(entry, checkpoint)
    analysis = analyze_performance(product_name, channel, metrics.get("engagement_score", 0.0),
                                    checkpoint=checkpoint)
    next_strategy = synthesize_strategy(product_name, channel, analysis, target=topic_index.load_target(product_name))
    return metrics, analysis, next_strategy


def _finalize_analysis(entry: dict[str, Any], checkpoint: str, metrics: dict[str, Any],
                        analysis: AnalysisResult, next_strategy: str) -> None:
    """The decision-time half: shared by _run_review_terminal() (called
    right after its own confirm_with_human() call) and
    apply_analysis_decision() (called from Streamlit once the human
    clicks Confirm/Disagree). Whether this counts as a Modiqo success or
    failure is decided by scoring.judge()'s gates alone, same as before
    this split — the human's confirm/disagree choice only ever affects
    `analysis.human_confirmed`/`human_note` (stored either way, see
    analysis.confirm_with_human()'s own docstring), never which of
    capture_success/capture_failure runs.
    """
    product_name, channel = entry["product"], entry["channel"]
    final_text = entry["final_text"]
    reviewer_note = entry.get("reviewer_note", "")

    audit.log_event("analysis", "recorded", product=product_name, channel=channel,
                     checkpoint=checkpoint, trend=analysis.trend,
                     n_prior_posts=analysis.n_prior_posts, human_confirmed=analysis.human_confirmed)

    # 2026-09-20: replaces `ctr >= SUCCESS_CTR_THRESHOLD`. The verdict is
    # now one of five states from scoring.judge() against a real baseline
    # of comparable posts, not a single absolute number — see scoring.py.
    verdict = judge(metrics, comparable_history(channel, checkpoint,
                                                 exclude_post_ids={entry.get("post_id")}))
    print(f"[{verdict.state}] {verdict.reason}")
    audit.log_event("modiqo", "judged", product=product_name, channel=channel,
                     checkpoint=checkpoint, state=verdict.state, score=verdict.score,
                     baseline_n=verdict.baseline_n, baseline_p75=verdict.baseline_p75)

    if verdict.is_win:
        # Only a win reaches capture_success, which is also what fixes the
        # old winning_text overwrite: every checkpoint used to land here
        # whenever it cleared the absolute threshold, so a worse post
        # routinely overwrote the best-known one. `below_baseline` and
        # `likes_only` now cannot reach it at all.
        play = capture_success(product_name, channel, final_text, metrics, reviewer_note,
                                improvement_note=next_strategy, analysis=analysis.as_dict(),
                                checkpoint=checkpoint)
        audit.log_event("modiqo", "success.captured", product=product_name, channel=channel,
                         checkpoint=checkpoint, runs=play["runs"], state=verdict.state)
    else:
        # Still recorded, with the state named: a `hypothesis` row is not
        # a failure, it is "there was nothing to compare against", and
        # that distinction is the whole point of gate 1.
        reason = f"{verdict.state} at {checkpoint} checkpoint — {verdict.reason}"
        capture_failure(product_name, channel, final_text, reason,
                         improvement_note=next_strategy, analysis=analysis.as_dict(),
                         checkpoint=checkpoint, metrics=metrics)
        audit.log_event("modiqo", "failure.captured", product=product_name, channel=channel,
                         checkpoint=checkpoint, reason=reason, state=verdict.state)

    tracking_review.mark_checkpoint_done(entry, checkpoint)


def _run_review_terminal(entry: dict[str, Any], checkpoint: str) -> None:
    """The original step_checkpoint_analyze() flow, unchanged in
    behavior: pull + analyze, block on confirm_with_human() right here in
    the terminal, then finalize immediately.
    """
    metrics, analysis, next_strategy = _pull_and_analyze(entry, checkpoint)
    analysis = confirm_with_human(analysis, next_strategy)
    _finalize_analysis(entry, checkpoint, metrics, analysis, next_strategy)


def _run_review_streamlit(entry: dict[str, Any], checkpoint: str) -> None:
    """2026-09-18 Phase 5 extension: pull + analyze eagerly (same as the
    terminal path), then queue the result for streamlit_app.py's Analysis
    review tab instead of blocking here — no confirm_with_human(), no
    capture, no mark_checkpoint_done() until apply_analysis_decision()
    runs once the human actually decides.
    """
    metrics, analysis, next_strategy = _pull_and_analyze(entry, checkpoint)
    analysis_queue.queue_analysis(entry["post_id"], checkpoint, entry, metrics, analysis.as_dict(), next_strategy)
    audit.log_event("analysis_queue", "queued", post_id=entry["post_id"], checkpoint=checkpoint)


def apply_analysis_decision(queue_entry: dict[str, Any], confirmed: bool, note: str) -> None:
    """Called from streamlit_app.py's Confirm/Disagree buttons — the
    decision-time counterpart to _run_review_terminal()'s inline
    confirm_with_human() call, mirroring how that page's Approve button
    calls step5_publish() directly for drafts. Rebuilds the AnalysisResult
    queue_analysis() serialized, applies the human's decision the same way
    analysis.confirm_with_human() does, runs the same finalize logic the
    terminal path runs right after its own input() call, then marks this
    queue entry decided.
    """
    analysis = AnalysisResult(**queue_entry["analysis"])
    analysis.human_confirmed = confirmed
    analysis.human_note = note
    entry = queue_entry["entry"]
    checkpoint = queue_entry["checkpoint"]
    metrics = queue_entry["metrics"]
    next_strategy = queue_entry["recommendation"]
    _finalize_analysis(entry, checkpoint, metrics, analysis, next_strategy)
    analysis_queue.update_entry(
        queue_entry["post_id"], checkpoint,
        status="confirmed" if confirmed else "disagreed", human_note=note,
    )


def refresh_metrics() -> int:
    """`contentmaster refresh` — re-reads engagement for every tracked post
    under 30 days old and appends the new reading to
    metrics/post_metrics.jsonl.

    Why this exists as its own command (2026-09-19): metrics were only
    ever pulled inside a checkpoint pass, and a checkpoint runs once per
    post per tier — `tracking_review.find_due()` skips anything already in
    checkpoints_done. So a post was measured at 24h and then never looked
    at again, permanently. That is not a timing bug (the real pulls landed
    24.7-50.9h after publish, which is correct); it is that nothing ever
    took a second reading. On a low-reach account, likes arrive from the
    discover feed days later, so the frozen number was systematically
    low — 1 like recorded where the account had 6 (docs/ERA0_SNAPSHOT.md).

    Deliberately side-effect free apart from the appended rows: no
    analysis, no model calls, no human decision, no capture_success /
    capture_failure, no checkpoints_done change. That is what makes it
    safe to run as often as you like, and it is the separation that was
    missing. Posts found to be deleted are marked and dropped from all
    future polling.
    """
    tracked = tracking_review.find_refreshable()
    if not tracked:
        print("No tracked posts under 30 days old to refresh.")
        return 0

    by_channel: dict[str, list[dict[str, Any]]] = {}
    for entry in tracked:
        by_channel.setdefault(entry["channel"], []).append(entry)

    n_written = n_deleted = 0
    for channel, entries in by_channel.items():
        if channel not in PLATFORMS:
            continue
        by_ref = {e["post_ref"]: e for e in entries}
        try:
            readings = get_platform(channel).get_post_metrics_batch(list(by_ref))
        except (PlatformConfigError, PlatformAPIError) as e:
            # No mock fallback here, unlike the checkpoint pull: a refresh
            # that cannot reach the platform has nothing to say, and
            # writing invented numbers is the exact failure this command
            # was built to stop.
            print(f"[warn] Could not refresh {channel} metrics ({e}); skipped, nothing written.")
            continue
        for post_ref, entry in by_ref.items():
            real = readings.get(post_ref)
            if real is None:
                tracking_review.mark_deleted(entry, detail="absent from a batch metrics refresh")
                audit.log_event(channel, "post.deleted", post_id=entry["post_id"],
                                 post_ref=post_ref, detected_by="refresh")
                print(f"[deleted] {entry['product']} / {post_ref} — gone from {channel}, "
                      "marked deleted and no longer polled.")
                n_deleted += 1
                continue
            reading = _reading_from_counts(channel, "refresh", real)
            metrics_store.write_metrics(entry["post_id"], channel, reading)
            audit.log_event(channel, "metrics.refreshed", post_id=entry["post_id"], **real)
            n_written += 1
            print(f"  {entry['product']} / {entry['post_id']}: "
                  f"{real['like_count']} like(s), {real['repost_count']} repost(s), "
                  f"{real['reply_count']} reply(ies)")

    print(f"\nRefreshed {n_written} post(s); {n_deleted} found deleted.")
    return 0


def run_review(checkpoint: str = "24h", terminal: bool = False) -> int:
    """`contentmaster review` — scans plays/_tracking_review.jsonl for
    posts due at this checkpoint (past CHECKPOINT_MIN_AGE and not already
    processed at this tier — no upper window, a late run still processes
    them). Manually triggered for now (or from the user's own launchd/
    cron) — real automatic in-process scheduling is still TBD, see
    docs/SCHEDULE.md Phase 9.

    `terminal` (2026-09-18, Phase 5 extension, /grill-me design session —
    see docs/LOG.md): default False, meaning each due post's analysis is
    queued and confirmed in a browser (see _run_review_streamlit);
    `terminal=True` keeps the original inline confirm_with_human() flow,
    with no browser involved at all — same flag semantics as `run()`'s
    own `terminal` parameter.

    Before processing anything, each due post is checked against
    analysis_queue — one already pending a human decision there (from an
    earlier browser invocation) is skipped rather than re-pulled and
    re-analyzed, regardless of which mode *this* invocation is running
    in; otherwise a still-undecided browser item could get silently
    double-processed and double-captured once the human finally answers.
    """
    due = tracking_review.find_due(checkpoint)
    if not due:
        print(f"No posts due for the {checkpoint} checkpoint.")
        return 0
    print(f"{len(due)} post(s) due for the {checkpoint} checkpoint.")
    n_queued = 0
    # 2026-09-21 (real usage bug): counted separately from n_queued, and
    # the browser now opens for either. Before this, a run where EVERY due
    # post was already pending left n_queued at 0, so launch_streamlit()
    # never ran — while the skip message below told the operator to go
    # open localhost:8501, which nothing had started. The server also
    # shuts itself down when idle (see streamlit_app.py), so "it was still
    # up from last time" is not a safe assumption either.
    n_already_pending = 0
    for entry in due:
        if analysis_queue.find_pending_for(entry["post_id"], checkpoint) is not None:
            n_already_pending += 1
            print(f"\n--- {entry['product']} / {entry['channel']} ({entry['post_id']}) ---")
            print("[skip] Already pending a human decision in the browser review queue.")
            continue
        print(f"\n--- {entry['product']} / {entry['channel']} ({entry['post_id']}) ---")
        try:
            if terminal:
                _run_review_terminal(entry, checkpoint)
            else:
                _run_review_streamlit(entry, checkpoint)
                n_queued += 1
        except PostWasDeleted:
            # Already recorded and explained by _pull_checkpoint_metrics();
            # just move on to the next due post rather than aborting the run.
            continue
        except MetricsUnavailable:
            # Deliberately NOT marked done — the post stays due so the next
            # run retries it. Already explained on stdout and in audit.
            continue
        except ReviewInterrupted:
            print("\n[stopped] Review interrupted — remaining due post(s) skipped this run.")
            break
    if n_queued or n_already_pending:
        parts = []
        if n_queued:
            parts.append(f"{n_queued} post(s)' analysis queued for review")
        if n_already_pending:
            parts.append(f"{n_already_pending} already waiting from an earlier run")
        print("\n" + "; ".join(parts) + ".")
        launch_streamlit()
    return 0


def _extract_topics_and_generate(
    whitepaper_path: str, channel: str, product_name: str, features: list[str], n_posts: int,
    target_region: str | None = None, target_audience: str | None = None,
) -> list[dict[str, Any]] | None:
    """Shared by both the terminal path and the streamlit path (2026-09-17
    Phase 5 design, see docs/LOG.md) — step1 and step3 are exactly the
    same either way, only what happens to the resulting drafts differs.
    Returns None if a ReviewInterrupted happened during topic review (the
    caller should stop the whole run then, same as before).

    `target_region`/`target_audience` (2026-09-18, see
    topic_index.load_target/save_target): only touched when actually
    passed this run, so `contentmaster run` with no --target-* flags keeps
    using whatever this product's target was last set to (None if never
    set). Testing phase only exercises `region` in practice.
    """
    # Tolerate a stray leading/trailing space in the path here as well as
    # in run() — this is the function that actually reads the file, and
    # it is the shared entry point for both the terminal and browser paths.
    whitepaper_path = whitepaper_path.strip()
    if target_region is not None or target_audience is not None:
        topic_index.save_target(product_name, target_region, target_audience)
    target = topic_index.load_target(product_name)

    topics_all: list[dict[str, Any]] = []
    try:
        topics_all = step1_cognee_extract_topics(whitepaper_path, product_name=product_name)
        if not topics_all:
            print("[warn] Cognee returned no extracted topics; continuing with supplied product info.")
    except ReviewInterrupted:
        print("\n[stopped] Topic review interrupted (stdin closed or Ctrl+C) — run aborted before any drafts.")
        return None
    except Exception as e:  # noqa: BLE001 — surfaced to the operator, pipeline continues
        audit.log_event("cognee", "extract.failed", error=str(e))
        print(f"[warn] Cognee extraction failed ({e}); continuing with supplied product info.")

    # 2026-09-19 (code scan): only built when there are no topics, because
    # a topic-grounded draft is strictly better — this is the fallback,
    # not a second input. Read locally on purpose (document.read_text, no
    # Cognee, no network): the entire reason it exists is to still work
    # when Cognee is the thing that is down.
    context = ""
    if not topics_all:
        context = _fallback_context(whitepaper_path, product_name, features)
        if context:
            print("[info] No topic index — grounding this run's drafts in the whitepaper text "
                  "read locally instead. Start Cognee to get topic-scoped drafts back.")

    return step3_generate_drafts(product_name, features, channel, topics_all=topics_all, n=n_posts,
                                  target=target, context=context)


def _fallback_context(whitepaper_path: str, product_name: str, features: list[str]) -> str:
    """Grounding text for a run with no topic index. The document's own
    words come first (that is the real source material); the product name
    and features are appended so the model still has *something* concrete
    to write from even when the document itself cannot be read locally
    (e.g. a PDF without the optional `pypdf` installed — see document.py).
    """
    parts = []
    doc_text = document.read_text(whitepaper_path, max_chars=document.PROMPT_MAX_CHARS)
    if doc_text:
        parts.append(f"Source document for '{product_name}':\n{doc_text}")
        audit.log_event("document", "read_local", file=whitepaper_path, chars=len(doc_text))
    else:
        audit.log_event("document", "read_local.empty", file=whitepaper_path)
    if features:
        parts.append(f"Known features of '{product_name}': " + "; ".join(features))
    return "\n\n".join(parts)


def _run_terminal(product_name: str, channel: str, drafts: list[dict[str, Any]]) -> None:
    """The original review flow, unchanged: text review then image review
    then publish then queue, one draft at a time, right here in the
    terminal.
    """
    results: list[dict[str, Any]] = []
    for i, draft in enumerate(drafts, start=1):
        print(f"\n########## Draft {i} of {len(drafts)} ##########")
        try:
            decision = step4_human_review(draft, product_name)
            if not decision.approved:
                capture_failure(product_name, channel, draft["text"], decision.reviewer_note or "rejected")
                results.append({"draft": i, "status": "rejected", "text": draft["text"], "channel": channel})
                continue
            image, image_alt = step4b_generate_image(draft, decision.final_text)
            published = step5_publish(draft, decision.final_text, image=image, image_alt=image_alt)
            step6_queue_for_review(product_name, channel, published,
                                    edited=decision.edited, reviewer_note=decision.reviewer_note)
        except PublishFailed as e:
            # Nothing was sent. Treated as a stop for this draft rather
            # than a crash: the rest of the batch is still worth
            # reviewing. Not written to _failures.jsonl — that file is the
            # "this content underperformed" signal, and a 22-character
            # overrun is a mechanical error, not a quality verdict.
            print(f"\n[not published] {e}")
            results.append({"draft": i, "status": f"failed ({e.failure.kind})",
                             "text": draft["text"], "channel": channel})
            continue
        except ReviewInterrupted:
            print(f"\n[stopped] Review interrupted at draft {i} of {len(drafts)} — "
                  f"{len(drafts) - i} remaining draft(s) skipped.")
            break
        results.append({
            "draft": i,
            "status": published["status"],
            "text": decision.final_text,
            "channel": channel,
            "post_ref": published.get("post_ref"),
            "source": draft.get("source", "unknown"),
        })

    audit.log_event("pipeline", "run.done")
    _print_run_summary(results)


def _run_streamlit(product_name: str, channel: str, drafts: list[dict[str, Any]]) -> None:
    """2026-09-17 Phase 5 design: image generated eagerly for every draft
    right here (text and image ready together, since streamlit cannot
    pause mid review to generate reactively), queued for
    streamlit_app.py to show, then the streamlit server is launched and
    opened automatically. No inline review here at all, unlike the
    terminal path.
    """
    out_dir = settings.data_root / "generated_images"
    out_dir.mkdir(parents=True, exist_ok=True)

    for draft in drafts:
        prompt = draft.get("brief") or draft["text"]
        image_bytes = image_generator.generate_image(prompt)
        image_path = None
        if image_bytes:
            image_path = str(out_dir / f"{draft['id']}.png")
            Path(image_path).write_bytes(image_bytes)
        audit.log_event("image_generator", "generated_for_queue", draft_id=draft["id"],
                         has_image=bool(image_bytes))
        draft_queue.queue_draft(
            draft["id"], product_name, channel, draft["text"],
            topic=draft.get("topic", ""), brief=draft.get("brief", ""), image_path=image_path,
            source=draft.get("source", "unknown"),
            labels={k: draft.get(k) for k in
                    ("topic_reason", "characteristics", "test_axis", "test_arm",
                     "evidence_used", "evidence_against") if draft.get(k) is not None},
        )

    audit.log_event("pipeline", "run.queued_for_streamlit", n=len(drafts))
    # 2026-09-19 (code scan): _print_run_summary() only ever ran on the
    # --terminal path, so the DEFAULT path ended with a bare one-liner and
    # no indication of what was actually generated — including, crucially,
    # whether these drafts came from the LLM or the fixed template.
    _print_queue_summary(drafts)
    launch_streamlit()


def _print_queue_summary(drafts: list[dict[str, Any]]) -> None:
    """End-of-run report for the browser path — the counterpart to
    _print_run_summary(), which reports on drafts that were reviewed and
    published inline. Nothing here is reviewed yet, so this reports what
    was queued and, above all, where each draft came from.
    """
    print("\n" + "=" * 60)
    print(f"{len(drafts)} DRAFT(S) QUEUED FOR REVIEW")
    print("=" * 60)
    for i, d in enumerate(drafts, start=1):
        source = d.get("source", "unknown")
        label = {
            "topics": "generated, grounded in an extracted topic",
            "context": "generated from the whitepaper text (no topic index)",
            "template": "NOT generated — fixed template, see the warning above",
        }.get(source, source)
        print(f"\nDraft {i} [{source}] — {label}")
        if d.get("topic"):
            print(f"  topic: {d['topic']}")
        print(f"  text : {d['text'][:100]}")
    if any(d.get("source") == "template" for d in drafts):
        print("\n[!] At least one draft is the canned template, not real generated content. "
              "Check that Cognee (localhost:8000) and Ollama (localhost:11434) are both up.")
    print("\nOpening the review page — approve, edit, or reject there.")


def launch_streamlit() -> None:
    """Starts streamlit_app.py as its own process, not headless, so it
    opens your default browser on its own. Prints the url too as a
    fallback in case auto open does not work for some reason.

    2026-09-18 (Phase 5 extended to `contentmaster review`, /grill-me
    design session, see docs/LOG.md): shared by both `run()` and
    `run_review()` now, both of which can independently decide to open a
    browser in the same session — without a check, a second call would
    try to bind an already-used port 8501. Streamlit's own resulting
    error used to be invisible anyway (stderr went to DEVNULL below), so
    this also fixes a real silent-failure gap, not just the new
    double-launch case. A plain socket probe is enough here (matches this
    project's "just enough, not a pidfile" style elsewhere — see
    topic_index.py's source_hash cache, the append-only queue files).

    2026-09-19 (real usage bug, see docs/LOG.md): the already-running
    branch used to only print a message and return — no tab actually
    opened, so a second `contentmaster run`/`review` invocation (the
    common case: server from an earlier call is still up) looked like
    "it doesn't open automatically" even though the *first* call's
    Streamlit-launched auto-open genuinely worked. `webbrowser.open()`
    now runs on both branches — only whether a *new server process* gets
    started differs.
    """
    import socket
    import subprocess
    import webbrowser

    url = "http://localhost:8501"

    try:
        with socket.create_connection(("localhost", 8501), timeout=0.5):
            print(f"Streamlit is already running — opening {url}.")
            webbrowser.open(url)
            return
    except OSError:
        pass

    app_path = settings.project_root / "streamlit_app.py"
    subprocess.Popen(
        ["streamlit", "run", str(app_path)],
        cwd=str(settings.project_root),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print("Opening the review page in your browser (http://localhost:8501 if it does not open on its own).")


def run(whitepaper_path: str, channel: str = "x", product_name: str | None = None,
        features: list[str] | None = None, n_posts: int = 1, terminal: bool = False,
        target_region: str | None = None, target_audience: str | None = None) -> None:
    """`n_posts` (2026-09-16): default changed 3 -> 1 per the user, while
    still testing — reviewing 3 drafts (each with its own text review,
    possible image review) every run was too much at this stage. Still
    overridable (`contentmaster run --posts N`) for when that's no longer
    true.

    `terminal` (2026-09-17, Phase 5 design, see docs/LOG.md): default is
    False now, meaning drafts get queued and reviewed in a browser (see
    _run_streamlit) — pass `terminal=True` for the original CLI review
    flow instead, with no browser involved at all.

    `target_region`/`target_audience` (2026-09-18, see
    topic_index.load_target/save_target and draft_generator._target_
    instruction): who this product's posts should be written for. Only
    updates the stored per-product default when actually passed here;
    omit both to keep using whatever was set last.
    """
    # 2026-09-19 (code scan): a real run passed `"cat.rtf "` — one stray
    # trailing space, pasted from a file picker — and every downstream
    # read failed on it. Cheap to tolerate, and the failure it caused
    # (silent fall back to template drafts) was expensive to diagnose.
    whitepaper_path = whitepaper_path.strip()
    audit.log_event("pipeline", "run.start", whitepaper=whitepaper_path, channel=channel)

    # Step 1 and step 3: same either way, see _extract_topics_and_generate.
    product_name = product_name or Path(whitepaper_path).stem

    # 2026-09-20: say out loud whether this name has been used before.
    # The name is the key every play file, topic index and history row
    # hangs off, and it is re-typed by hand every run — four spellings of
    # the same cat whitepaper split the history four ways and made every
    # analysis report "no prior posts". Nothing here renames anything;
    # the name stays a human decision, it is just no longer an invisible
    # one. See product_registry.py.
    try:
        source_hash = topic_index.file_hash(whitepaper_path)
    except OSError:
        source_hash = None  # unreadable path is reported properly further down
    print(f"[product] {describe_product(product_name)}")
    remember_product(product_name, source_hash=source_hash, whitepaper=whitepaper_path)
    if not features:
        # 2026-09-19 (code scan): these describe THIS PIPELINE, not
        # whatever product is being marketed — they date from when the
        # whitepaper being processed was this project's own. Left in as a
        # last-resort placeholder, but never silently: a real run produced
        # "How to Use a Cat — compound memory. Built for teams who ship
        # fast." and nothing anywhere said where "compound memory" came
        # from. Pass --features to replace them.
        features = list(PLACEHOLDER_FEATURES)
        print(f"[warn] No --features given for '{product_name}' — falling back to the built-in "
              f"placeholders {features}, which describe this pipeline, not your product. "
              f"They are only used if generation cannot be grounded any other way.")
    drafts = _extract_topics_and_generate(whitepaper_path, channel, product_name, features, n_posts,
                                           target_region=target_region, target_audience=target_audience)
    if drafts is None:
        return

    if terminal:
        _run_terminal(product_name, channel, drafts)
    else:
        _run_streamlit(product_name, channel, drafts)


def _print_run_summary(results: list[dict[str, Any]]) -> None:
    """Prints a clear end-of-run report so it's obvious the pipeline
    finished (rather than looking hung). No metrics/feedback here anymore
    (2026-09-15) — that would mean pulling numbers seconds after publish,
    which is noise, not signal (see module docstring). Real feedback comes
    from `contentmaster review` once a checkpoint's worth of time passes.
    """
    print("\n" + "=" * 60)
    print(f"PIPELINE FINISHED — {len(results)} draft(s) processed")
    print("=" * 60)
    any_published = False
    for r in results:
        print(f"\nDraft {r['draft']}: {r['status']}  [source: {r.get('source', 'unknown')}]")
        print(f"  text: {r['text'][:100]}")
        # "view live" link formatting is inherently platform-specific —
        # add a case per platform here as each one gets a real adapter.
        if r.get("post_ref") and r.get("channel") == "bluesky":
            post_id = r["post_ref"].rsplit("/", 1)[-1]
            handle = settings.bluesky.handle or "<handle>"
            print(f"  view live: https://bsky.app/profile/{handle}/post/{post_id}")
        if r["status"].startswith("published"):
            any_published = True
    if any_published:
        print("\nQueued for review — run `contentmaster review` again in 24 hours "
              "to see the first real feedback, once the numbers have had time to "
              "mean something (see docs/SCHEDULE.md Phase 9 for why).")
    print()
