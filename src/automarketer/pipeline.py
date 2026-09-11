"""Orchestrates the full loop from the white paper (§3):

  Cognee -> HydraDB -> RocketRide -> [human review] -> (publish)
    -> hotdata.dev -> Modiqo -> write back to HydraDB

Run with: python run_pipeline.py --whitepaper "Marketing hack white paper.pdf"
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from . import audit
from .cognee_client import CogneeClient
from .hotdata_client import HotdataClient
from .human_loop import review_draft
from .hydradb_client import HydraDBClient
from .modiqo_play import capture_failure, capture_success, find_play
from .rocketride_client import RocketRide

SUCCESS_CTR_THRESHOLD = 0.02  # click-through rate above this counts as a "win" for Modiqo


def step1_cognee_extract(whitepaper_path: str, dataset_label: str) -> dict[str, Any]:
    """Cognee: ECL pipeline over the raw whitepaper."""
    cognee = CogneeClient()
    audit.log_event("cognee", "add.start", file=whitepaper_path)
    cognee.add_document(whitepaper_path, labels=dataset_label)
    audit.log_event("cognee", "add.done")

    audit.log_event("cognee", "cognify.start")
    cognee.cognify()
    audit.log_event("cognee", "cognify.done")

    audit.log_event("cognee", "search.start", query="product name, key features, target ICP")
    result = cognee.search("What is the product's name, its key features, and its target ICP?")
    audit.log_event("cognee", "search.done")
    return result


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


def step3_rocketride_or_replay(product_name: str, features: list[str], channel: str) -> list[dict[str, Any]]:
    """RocketRide generates drafts — unless Modiqo already has a winning play
    for this (product, channel), in which case we replay it directly and
    skip generation entirely. This is the visible "run #2 is cheaper" proof.
    """
    play = find_play(product_name, channel)
    if play:
        audit.log_event("modiqo", "play.replayed", product=product_name, channel=channel,
                         runs_so_far=play["runs"])
        return [{"id": "replayed", "channel": channel, "text": play["winning_text"], "status": "draft"}]

    rr = RocketRide()
    audit.log_event("rocketride", "draft.start", product=product_name, channel=channel)
    drafts = rr.draft_posts({"name": product_name, "features": features}, channel)
    audit.log_event("rocketride", "draft.done", n=len(drafts))
    return drafts


def step4_human_review(draft: dict[str, Any]) -> Any:
    decision = review_draft(draft)
    audit.log_event("human_loop", "reviewed", draft_id=draft["id"],
                     approved=decision.approved, edited=decision.edited,
                     note=decision.reviewer_note)
    return decision


def step5_publish(draft: dict[str, Any], final_text: str) -> dict[str, Any]:
    """Simulated publish — wiring a real posting API call needs explicit
    per-post approval (see the operating rules this assistant follows), so
    this stays a local, clearly-labeled stand-in until you wire a real
    channel adapter (e.g. via a RocketRide .pipe).
    """
    published = {**draft, "text": final_text, "status": "published (simulated)"}
    audit.log_event("publish", "simulated", draft_id=draft["id"], channel=draft["channel"])
    return published


def step6_hotdata_metrics(post_id: str, channel: str) -> dict[str, Any]:
    """White paper §5 MVP scope: hotdata.dev returns metrics "透過模擬或簡單真實數據"
    (simulated or simple real data) — there's no live posted campaign to pull
    real engagement from yet, so a simulated reading is generated here and
    then written through hotdata.dev for real (a real CLI `load --append`
    call), then read back with a real `hotdata query` SQL call. hotdata is
    doing genuine storage+query work; only the underlying engagement number
    itself is simulated.
    """
    hd = HotdataClient()
    simulated = hd._mock_metrics(post_id)  # stand-in for live engagement data
    hd.write_metrics(post_id, channel, simulated)
    metrics = hd.get_metrics(post_id)
    audit.log_event("hotdata", "metrics.fetched", post_id=post_id, metrics=metrics)
    return metrics


def step7_modiqo_capture(product_name: str, channel: str, final_text: str,
                          metrics: dict[str, Any], approved: bool, reviewer_note: str) -> None:
    db = HydraDBClient()
    if approved and metrics.get("ctr", 0) >= SUCCESS_CTR_THRESHOLD:
        play = capture_success(product_name, channel, final_text, metrics, reviewer_note)
        db.link("Product", product_name, {"name": product_name}, "HAS_PLAY",
                 "Play", f"{product_name}::{channel}", {"channel": channel, "runs": play["runs"]})
        audit.log_event("modiqo", "success.captured", product=product_name, channel=channel,
                         runs=play["runs"])
    else:
        reason = reviewer_note or f"ctr {metrics.get('ctr')} below threshold {SUCCESS_CTR_THRESHOLD}"
        capture_failure(product_name, channel, final_text, reason)
        audit.log_event("modiqo", "failure.captured", product=product_name, channel=channel,
                         reason=reason)


def run(whitepaper_path: str, channel: str = "x", product_name: str | None = None,
        features: list[str] | None = None, icp: str = "B2B deep-tech teams") -> None:
    audit.log_event("pipeline", "run.start", whitepaper=whitepaper_path, channel=channel)

    # Steps 1: Cognee. (Falls back to caller-supplied product/features if the
    # LLM key isn't configured yet on the Cognee container — see README.)
    product_name = product_name or Path(whitepaper_path).stem
    features = features or ["compound memory", "muscle-memory replay", "human-in-the-loop safety"]
    try:
        step1_cognee_extract(whitepaper_path, dataset_label="whitepaper")
    except Exception as e:  # noqa: BLE001 — surfaced to the operator, pipeline continues
        audit.log_event("cognee", "extract.failed", error=str(e))
        print(f"[warn] Cognee extraction failed ({e}); continuing with supplied product info.")

    # Step 2: HydraDB
    step2_hydradb_persist(product_name, features, icp)

    # Step 3: RocketRide (or Modiqo replay)
    drafts = step3_rocketride_or_replay(product_name, features, channel)

    # Steps 4-7: human review -> publish -> hotdata -> Modiqo, per draft
    for draft in drafts:
        decision = step4_human_review(draft)
        if not decision.approved:
            capture_failure(product_name, channel, draft["text"], decision.reviewer_note or "rejected")
            continue
        published = step5_publish(draft, decision.final_text)
        metrics = step6_hotdata_metrics(published["id"], channel)
        step7_modiqo_capture(product_name, channel, decision.final_text, metrics,
                              approved=True, reviewer_note=decision.reviewer_note)

    audit.log_event("pipeline", "run.done")


def main() -> None:
    parser = argparse.ArgumentParser(description="AutoMarketer.ai pipeline (hackathon MVP)")
    parser.add_argument("--whitepaper", required=True)
    parser.add_argument("--channel", default="x")
    parser.add_argument("--product-name", default=None)
    args = parser.parse_args()
    run(args.whitepaper, channel=args.channel, product_name=args.product_name)


if __name__ == "__main__":
    main()
