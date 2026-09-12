"""Orchestrates the full loop from the white paper (§3):

  Cognee -> HydraDB -> RocketRide -> [human review] -> (publish)
    -> hotdata.dev -> Modiqo -> write back to HydraDB

Run with: python run_pipeline.py --whitepaper "Marketing hack white paper.pdf"
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from . import audit
from .bluesky_client import BlueskyAPIError, BlueskyClient, BlueskyConfigError
from .cognee_client import CogneeClient
from .config import CogneeSettings, settings
from .hotdata_client import HotdataClient
from .human_loop import ReviewInterrupted, review_draft
from .hydradb_client import HydraDBClient
from .improve import generate_improvement_note
from .modiqo_play import capture_failure, capture_success, find_play
from .rocketride_client import RocketRide

SUCCESS_CTR_THRESHOLD = 0.02  # click-through rate above this counts as a "win" for Modiqo


def _dataset_slug(product_name: str) -> str:
    """Each product gets its own Cognee dataset so runs for different
    products/whitepapers never cross-contaminate each other's knowledge
    graph — search() only ever sees what was ingested for *this* product.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", product_name.lower()).strip("-")
    return slug or "automarketer"


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
    """RocketRide generates drafts — unless Modiqo already has a winning play
    for this (product, channel), in which case we replay it directly and
    skip generation entirely. This is the visible "run #2 is cheaper" proof.

    `context` (Cognee's real extraction from the uploaded document, if any)
    is what lets RocketRide.draft_posts() ground the copy in the actual
    source material instead of the generic feature-name template.
    """
    play = find_play(product_name, channel)
    if play:
        audit.log_event("modiqo", "play.replayed", product=product_name, channel=channel,
                         runs_so_far=play["runs"])
        return [{"id": "replayed", "channel": channel, "text": play["winning_text"], "status": "draft"}]

    rr = RocketRide()
    audit.log_event("rocketride", "draft.start", product=product_name, channel=channel,
                     grounded=bool(context))
    drafts = rr.draft_posts({"name": product_name, "features": features}, channel, context=context)
    audit.log_event("rocketride", "draft.done", n=len(drafts))
    return drafts


def step4_human_review(draft: dict[str, Any]) -> Any:
    decision = review_draft(draft)
    audit.log_event("human_loop", "reviewed", draft_id=draft["id"],
                     approved=decision.approved, edited=decision.edited,
                     note=decision.reviewer_note)
    return decision


def step5_publish(draft: dict[str, Any], final_text: str) -> dict[str, Any]:
    """channel == "bluesky" posts for real via the Bluesky client — gated on
    the human approval that already happened in step4 (human_loop.review_draft),
    which is this project's "explicit per-post approval" checkpoint. Every
    other channel stays a simulated, clearly-labeled stand-in until a real
    adapter is wired for it (e.g. via a RocketRide .pipe).
    """
    if draft["channel"] == "bluesky":
        try:
            bsky = BlueskyClient()
            result = bsky.publish_post(final_text)
            published = {**draft, "text": final_text, "status": "published", "bluesky_uri": result["uri"]}
            audit.log_event("publish", "bluesky.posted", draft_id=draft["id"], uri=result["uri"])
            return published
        except BlueskyConfigError as e:
            print(f"[warn] Bluesky not configured ({e}); falling back to simulated publish.")
        except BlueskyAPIError as e:
            print(f"[warn] Bluesky post failed ({e}); falling back to simulated publish.")

    published = {**draft, "text": final_text, "status": "published (simulated)"}
    audit.log_event("publish", "simulated", draft_id=draft["id"], channel=draft["channel"])
    return published


def step6_hotdata_metrics(published: dict[str, Any], channel: str) -> dict[str, Any]:
    """White paper §5 MVP scope: hotdata.dev returns metrics "透過模擬或簡單真實數據"
    (simulated or simple real data). When step5 actually posted to Bluesky,
    this pulls real engagement back from Bluesky and that's what gets
    written through hotdata.dev; otherwise (every other channel, or a
    Bluesky post that fell back to simulated) a simulated reading is used
    instead. Either way hotdata.dev itself does genuine storage+query work
    (a real CLI `load --append` call, then a real `hotdata query` read).
    """
    post_id = published["id"]
    hd = HotdataClient()
    reading = None
    if published.get("bluesky_uri"):
        try:
            bsky = BlueskyClient()
            real = bsky.get_post_metrics(published["bluesky_uri"])
            impressions = max(real["like_count"] + real["repost_count"] + real["reply_count"], 1)
            reading = {
                "source": "bluesky (live)",
                "post_id": post_id,
                "impressions": impressions,  # Bluesky doesn't expose impressions; approximated from engagement
                "clicks": real["like_count"],
                "conversions": real["repost_count"],
                "ctr": round(real["like_count"] / impressions, 4),
            }
            audit.log_event("bluesky", "metrics.pulled", **real)  # real already has "uri"
        except BlueskyAPIError as e:
            print(f"[warn] Could not pull Bluesky metrics ({e}); falling back to simulated reading.")

    if reading is None:
        reading = hd._mock_metrics(post_id)  # stand-in for live engagement data
    hd.write_metrics(post_id, channel, reading)
    metrics = hd.get_metrics(post_id)
    audit.log_event("hotdata", "metrics.fetched", post_id=post_id, metrics=metrics)
    return metrics


def step7b_llm_improve(product_name: str, channel: str, final_text: str, metrics: dict[str, Any]) -> str:
    """Ask the local LLM for one concrete improvement given this post's real
    (or simulated) metrics. Stored on the play record so the next run's
    RocketRide instructions/prompt can be built with it in mind.
    """
    note = generate_improvement_note(product_name, channel, final_text, metrics)
    audit.log_event("improve", "note.generated", product=product_name, channel=channel, note=note)
    return note


def step7_modiqo_capture(product_name: str, channel: str, final_text: str,
                          metrics: dict[str, Any], approved: bool, reviewer_note: str) -> None:
    db = HydraDBClient()
    improvement_note = step7b_llm_improve(product_name, channel, final_text, metrics)
    if approved and metrics.get("ctr", 0) >= SUCCESS_CTR_THRESHOLD:
        play = capture_success(product_name, channel, final_text, metrics, reviewer_note, improvement_note)
        db.link("Product", product_name, {"name": product_name}, "HAS_PLAY",
                 "Play", f"{product_name}::{channel}", {"channel": channel, "runs": play["runs"]})
        audit.log_event("modiqo", "success.captured", product=product_name, channel=channel,
                         runs=play["runs"])
    else:
        reason = reviewer_note or f"ctr {metrics.get('ctr')} below threshold {SUCCESS_CTR_THRESHOLD}"
        capture_failure(product_name, channel, final_text, reason, improvement_note)
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

    # Steps 4-7: human review -> publish -> hotdata -> Modiqo, per draft
    results: list[dict[str, Any]] = []
    for i, draft in enumerate(drafts, start=1):
        print(f"\n########## Draft {i} of {len(drafts)} ##########")
        try:
            decision = step4_human_review(draft)
        except ReviewInterrupted:
            print(f"\n[stopped] Review interrupted at draft {i} of {len(drafts)} — "
                  f"{len(drafts) - i} remaining draft(s) skipped.")
            break
        if not decision.approved:
            capture_failure(product_name, channel, draft["text"], decision.reviewer_note or "rejected")
            results.append({"draft": i, "status": "rejected", "text": draft["text"]})
            continue
        published = step5_publish(draft, decision.final_text)
        metrics = step6_hotdata_metrics(published, channel)
        step7_modiqo_capture(product_name, channel, decision.final_text, metrics,
                              approved=True, reviewer_note=decision.reviewer_note)
        results.append({
            "draft": i,
            "status": published["status"],
            "text": decision.final_text,
            "bluesky_uri": published.get("bluesky_uri"),
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
        if r.get("bluesky_uri"):
            post_id = r["bluesky_uri"].rsplit("/", 1)[-1]
            handle = settings.bluesky.handle or "<handle>"
            print(f"  view live: https://bsky.app/profile/{handle}/post/{post_id}")
        if r.get("metrics"):
            m = r["metrics"]
            print(f"  feedback: {m.get('clicks', 0)} likes-equivalent, "
                  f"{m.get('conversions', 0)} reposts-equivalent, ctr={m.get('ctr')} "
                  f"(source: {m.get('source')})")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="AutoMarketer.ai pipeline (hackathon MVP)")
    parser.add_argument("--whitepaper", required=True)
    parser.add_argument("--channel", default="x")
    parser.add_argument("--product-name", default=None)
    args = parser.parse_args()
    run(args.whitepaper, channel=args.channel, product_name=args.product_name)


if __name__ == "__main__":
    main()
