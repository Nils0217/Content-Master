"""Orchestrates the full loop (see docs/WHITEPAPER.md §2):

  Cognee -> HydraDB -> RocketRide -> [human review] -> (publish)
    -> track metrics -> analysis (cross-validated vs. warehouse history,
    human-confirmed) -> discuss (2 local models propose, 1 synthesizes)
    -> log (Modiqo) -> next run's generation reads it back

Run with: contentmaster run --whitepaper "Marketing hack white paper.pdf"
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import audit, metrics_store
from .analysis import analyze_performance
from .cognee_client import CogneeClient
from .config import CogneeSettings, settings
from .discuss import synthesize_strategy
from .human_loop import ReviewInterrupted, review_draft
from .hydradb_client import HydraDBClient
from .modiqo_play import capture_failure, capture_success, find_play
from .platforms.base import PlatformAPIError, PlatformConfigError
from .platforms.registry import PLATFORMS, get_platform
from .rocketride_client import RocketRide

SUCCESS_CTR_THRESHOLD = 0.02  # click-through rate above this counts as a "win" for Modiqo


def _dataset_slug(product_name: str) -> str:
    """Each product gets its own Cognee dataset so runs for different
    products/whitepapers never cross-contaminate each other's knowledge
    graph — search() only ever sees what was ingested for *this* product.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", product_name.lower()).strip("-")
    return slug or "contentmaster"


def step1_cognee_extract(whitepaper_path: str, dataset_label: str, product_name: str) -> str:
    """Cognee: ECL pipeline over the raw whitepaper. Returns the extracted
    context text (or "" if Cognee had nothing to say) — this is what makes
    step3's drafts actually grounded in the uploaded document instead of
    generic boilerplate.
    """
    cognee = CogneeClient(CogneeSettings(dataset=_dataset_slug(product_name)))
    audit.log_event("cognee", "add.start", file=whitepaper_path)
    cognee.add_document(whitepaper_path, labels=dataset_label)
    audit.log_event("cognee", "add.done")

    audit.log_event("cognee", "cognify.start")
    cognee.cognify()
    audit.log_event("cognee", "cognify.done")

    audit.log_event("cognee", "search.start", query="product name, key features, target ICP")
    result = cognee.search("What is the product's name, its key features, and its target ICP?")
    audit.log_event("cognee", "search.done")

    # result shape: [{"search_result": ["free text..."], ...}, ...]
    for entry in result or []:
        for text in entry.get("search_result", []):
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


def step2_hydradb_persist(product_name: str, features: list[str], icp: str) -> None:
    """HydraDB: durable Product -[:HAS_FEATURE]-> Feature and Product -[:TARGETS]-> ICP graph."""
    db = HydraDBClient()
    for feature in features:
        db.link("Product", product_name, {"name": product_name}, "HAS_FEATURE",
                 "Feature", feature, {"text": feature})
    db.link("Product", product_name, {"name": product_name}, "TARGETS",
             "ICP", icp, {"description": icp})
    audit.log_event("hydradb", "graph.persisted", product=product_name,
                     n_features=len(features), icp=icp)


def step3_rocketride_or_replay(
    product_name: str, features: list[str], channel: str, context: str = ""
) -> list[dict[str, Any]]:
    """RocketRide generates drafts. When Modiqo already has a play for this
    (product, channel) — i.e. a prior run's winning post + its real metrics
    + the LLM's improvement note — that gets fed back in as `prior` so this
    run writes a genuinely improved version instead of a cold-start draft.
    This is the "read results -> analysis -> new post -> another loop" cycle,
    not a plain replay: every run calls the LLM again on purpose, trading
    the old "run #2 is free" shortcut for content that actually compounds.

    `context` (Cognee's real extraction from the uploaded document, if any)
    is what lets RocketRide.draft_posts() ground the copy in the actual
    source material instead of the generic feature-name template.
    """
    play = find_play(product_name, channel)
    rr = RocketRide()
    audit.log_event("rocketride", "draft.start", product=product_name, channel=channel,
                     grounded=bool(context), improving_on_prior=bool(play))
    drafts = rr.draft_posts({"name": product_name, "features": features}, channel, context=context, prior=play)
    audit.log_event("rocketride", "draft.done", n=len(drafts))
    return drafts


def step4_human_review(draft: dict[str, Any]) -> Any:
    decision = review_draft(draft)
    audit.log_event("human_loop", "reviewed", draft_id=draft["id"],
                     approved=decision.approved, edited=decision.edited,
                     note=decision.reviewer_note)
    return decision


def step5_publish(draft: dict[str, Any], final_text: str) -> dict[str, Any]:
    """Posts for real on any *implemented* platform (see
    platforms/registry.py's PLATFORMS) — gated on the human approval that
    already happened in step4 (human_loop.review_draft), this project's
    "explicit per-post approval" checkpoint. A channel with no implemented
    adapter yet (see PLANNED_PLATFORMS), or one whose publish call errors,
    stays a simulated, clearly-labeled stand-in.
    """
    channel = draft["channel"]
    if channel in PLATFORMS:
        try:
            platform = get_platform(channel)
            result = platform.publish_post(final_text)
            post_ref = result.get("uri") or result.get("id")
            published = {**draft, "text": final_text, "status": "published", "post_ref": post_ref}
            audit.log_event("publish", f"{channel}.posted", draft_id=draft["id"], ref=post_ref)
            return published
        except PlatformConfigError as e:
            print(f"[warn] {channel} not configured ({e}); falling back to simulated publish.")
        except PlatformAPIError as e:
            print(f"[warn] {channel} post failed ({e}); falling back to simulated publish.")

    published = {**draft, "text": final_text, "status": "published (simulated)"}
    audit.log_event("publish", "simulated", draft_id=draft["id"], channel=channel)
    return published


def step6_track_metrics(published: dict[str, Any], channel: str) -> dict[str, Any]:
    """Pulls real engagement back when step5 actually posted to a real
    platform; otherwise (a channel with no implemented adapter, or a
    publish that fell back to simulated) records a clearly-labeled
    simulated reading instead. Either way this writes a real row to
    metrics/post_metrics.jsonl, which warehouse/'s dbt project reads
    directly off disk (see warehouse/models/staging/stg_post_metrics.sql)
    — no CLI, no account, no live DB connection needed here (hotdata.dev
    filled this role before; retired, see docs/SCHEDULE.md Phase 0).
    """
    post_id = published["id"]
    reading = None
    if published.get("post_ref") and channel in PLATFORMS:
        try:
            platform = get_platform(channel)
            real = platform.get_post_metrics(published["post_ref"])
            impressions = max(real["like_count"] + real["repost_count"] + real["reply_count"], 1)
            reading = {
                "source": f"{channel} (live)",
                "impressions": impressions,  # not every platform exposes impressions; approximated from engagement
                "clicks": real["like_count"],
                "conversions": real["repost_count"],
                "ctr": round(real["like_count"] / impressions, 4),
            }
            audit.log_event(channel, "metrics.pulled", **real)
        except PlatformAPIError as e:
            print(f"[warn] Could not pull {channel} metrics ({e}); falling back to simulated reading.")

    if reading is None:
        reading = metrics_store.mock_metrics(post_id)
    metrics_store.write_metrics(post_id, channel, reading)
    metrics = metrics_store.get_metrics(post_id)
    audit.log_event("metrics", "recorded", post_id=post_id, metrics=metrics)
    return metrics


def step7a_analyze_and_discuss(product_name: str, channel: str, metrics: dict[str, Any]) -> tuple[Any, str]:
    """track (already done — `metrics` is step6's output) -> ANALYSIS ->
    DISCUSS -> (log happens in step7_modiqo_capture, right after).

    Replaces the old single-model "improve" step, which asked one local
    LLM to eyeball this post's raw numbers and guess a suggestion — no
    comparison against history, no check on whether the *previous*
    suggestion actually correlated with anything moving. Now: analysis.py
    cross-validates against this product/channel's real timeline in the
    warehouse (with a human confirmation gate — see analysis.py), and
    discuss.py has two *different* local models each propose a strategy
    grounded in that analysis, synthesized into one final instruction —
    real model diversity, not one model talking to itself.
    """
    analysis = analyze_performance(product_name, channel, metrics.get("ctr", 0.0), interactive=True)
    audit.log_event("analysis", "recorded", product=product_name, channel=channel,
                     trend=analysis.trend, n_prior_posts=analysis.n_prior_posts,
                     human_confirmed=analysis.human_confirmed)
    next_strategy = synthesize_strategy(product_name, channel, analysis)
    return analysis, next_strategy


def step7_modiqo_capture(product_name: str, channel: str, final_text: str,
                          metrics: dict[str, Any], approved: bool, reviewer_note: str) -> None:
    db = HydraDBClient()
    analysis, next_strategy = step7a_analyze_and_discuss(product_name, channel, metrics)
    if approved and metrics.get("ctr", 0) >= SUCCESS_CTR_THRESHOLD:
        play = capture_success(product_name, channel, final_text, metrics, reviewer_note,
                                improvement_note=next_strategy, analysis=analysis.as_dict())
        db.link("Product", product_name, {"name": product_name}, "HAS_PLAY",
                 "Play", f"{product_name}::{channel}", {"channel": channel, "runs": play["runs"]})
        audit.log_event("modiqo", "success.captured", product=product_name, channel=channel,
                         runs=play["runs"])
    else:
        reason = reviewer_note or f"ctr {metrics.get('ctr')} below threshold {SUCCESS_CTR_THRESHOLD}"
        capture_failure(product_name, channel, final_text, reason,
                         improvement_note=next_strategy, analysis=analysis.as_dict())
        audit.log_event("modiqo", "failure.captured", product=product_name, channel=channel,
                         reason=reason)


def run(whitepaper_path: str, channel: str = "x", product_name: str | None = None,
        features: list[str] | None = None, icp: str = "B2B deep-tech teams") -> None:
    audit.log_event("pipeline", "run.start", whitepaper=whitepaper_path, channel=channel)

    # Steps 1: Cognee. (Falls back to caller-supplied product/features if the
    # LLM key isn't configured yet on the Cognee container — see README.)
    product_name = product_name or Path(whitepaper_path).stem
    features = features or ["compound memory", "muscle-memory replay", "human-in-the-loop safety"]
    context = ""
    try:
        context = step1_cognee_extract(whitepaper_path, dataset_label="whitepaper", product_name=product_name)
        if not context:
            print("[warn] Cognee returned no extracted text; continuing with supplied product info.")
    except Exception as e:  # noqa: BLE001 — surfaced to the operator, pipeline continues
        audit.log_event("cognee", "extract.failed", error=str(e))
        print(f"[warn] Cognee extraction failed ({e}); continuing with supplied product info.")

    # Step 2: HydraDB
    step2_hydradb_persist(product_name, features, icp)

    # Step 3: RocketRide (or Modiqo replay) — grounded in Cognee's real
    # extraction when available, so drafts reflect the uploaded document.
    drafts = step3_rocketride_or_replay(product_name, features, channel, context=context)

    # Steps 4-7: human review -> publish -> track metrics -> Modiqo, per draft
    results: list[dict[str, Any]] = []
    for i, draft in enumerate(drafts, start=1):
        print(f"\n########## Draft {i} of {len(drafts)} ##########")
        try:
            decision = step4_human_review(draft)
            if not decision.approved:
                capture_failure(product_name, channel, draft["text"], decision.reviewer_note or "rejected")
                results.append({"draft": i, "status": "rejected", "text": draft["text"], "channel": channel})
                continue
            published = step5_publish(draft, decision.final_text)
            metrics = step6_track_metrics(published, channel)
            # step7 also has a human-in-the-loop gate (analysis confirmation,
            # see analysis.py) — kept inside this same try so an interrupt
            # there stops just as cleanly as one during draft review.
            step7_modiqo_capture(product_name, channel, decision.final_text, metrics,
                                  approved=True, reviewer_note=decision.reviewer_note)
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
            "metrics": metrics,
        })

    audit.log_event("pipeline", "run.done")
    _print_run_summary(results)


def _print_run_summary(results: list[dict[str, Any]]) -> None:
    """Prints a clear end-of-run report so it's obvious the pipeline
    finished (rather than looking hung) and where to see real feedback.
    """
    print("\n" + "=" * 60)
    print(f"PIPELINE FINISHED — {len(results)} draft(s) processed")
    print("=" * 60)
    for r in results:
        print(f"\nDraft {r['draft']}: {r['status']}")
        print(f"  text: {r['text'][:100]}")
        # "view live" link formatting is inherently platform-specific —
        # add a case per platform here as each one gets a real adapter.
        if r.get("post_ref") and r.get("channel") == "bluesky":
            post_id = r["post_ref"].rsplit("/", 1)[-1]
            handle = settings.bluesky.handle or "<handle>"
            print(f"  view live: https://bsky.app/profile/{handle}/post/{post_id}")
        if r.get("metrics"):
            m = r["metrics"]
            print(f"  feedback: {m.get('clicks', 0)} likes-equivalent, "
                  f"{m.get('conversions', 0)} reposts-equivalent, ctr={m.get('ctr')} "
                  f"(source: {m.get('source')})")
    print()
