# ContentMaster — a compound marketing agent with real memory

> Originally built at a hackathon against five mandated sponsor tools
> (Cognee, HydraDB, hotdata.dev, RocketRide.ai, Modiqo.ai/Rote) plus Snyk,
> under the name "AutoMarketer.ai". This document describes the project
> going forward, as an independent, open-source, local-first system — not
> a hackathon submission. See `docs/LOG.md` for the history of how it got
> here (including the rename) and `Marketing hack white paper.pdf` for the
> original hackathon-era version.

## 1. Vision

Any input — a whitepaper, a video, a codebase, a podcast episode — goes in.
An AI agent pulls out whatever is actually useful, regardless of the
source format. It produces marketing material (text posts today; images
and video as the project grows). A human reviews it. What gets approved
gets published. Once real time has actually passed — not seconds after
publish — the agent pulls real performance against human-defined KPIs,
cross-validates it against this product/channel's history, proposes a
concrete improvement (or confirms the approach worked), and writes the
result into a durable memory layer. The next round of content generation
draws on that memory — not a cold start, not a frozen replay, a version
that acted on what was actually learned.

The project is open source, CLI design (`contentmaster` — see `README.md`).
As the dataset of (content → post → real
performance → improvement) accumulates, the goal is to package it as a
domain-specific plugin any LLM can attach to — via MCP — so the knowledge
compounds independently of which model is doing the writing.

## 2. The loop

**2026-09-15 redesign**: the loop used to pull "real" metrics and run the
full analysis seconds after publish — a post that's been live for a
few seconds has had no real chance to be seen by anyone (`impressions: 1,
ctr: 0.0` is a real example, not a bug), so every trend/verdict computed
that way was noise, not signal. The loop is now split into two halves
that run at different times, on purpose:

```
any input (doc / video / code / podcast)
        │
        ▼
   topic index (Cognee, but only re-extracted when the source file's
        content hash actually changes — otherwise zero Cognee calls;
        Cognee returns a *list* of topics now, not one fixed Q&A answer.
        Genuinely new topics get a quick human review — batch accept, or
        per-item edit/delete/type-in-your-own. src/contentmaster/topic_index.py)
        │
        ▼
   content generation: one post per *least-used* topic — real variety
        across runs from distinct extracted facts, not a hardcoded
        per-draft template (src/contentmaster/draft_generator.py)
        │
        ▼
   human review — [a]pprove / [e]dit (type the exact final text yourself)
        / [f]eedback (describe what should change, the LLM revises it,
        you review the result before it can be approved) / [r]eject
        ──reject──▶ logged as a negative example
        │ approve
        ▼
   image generation (2026-09-16, optional — Cloudflare Workers AI /
        FLUX.1-schnell, see §3): grounded in the final approved text +
        topic brief. Same brand-safety gate as text — a human looks at
        the generated image before it can be attached
        ([a]ttach / [r]egenerate / [s]kip text-only), never posted
        unreviewed. Skips silently (text-only) if not configured.
        │
        ▼
   publish (Bluesky as testing — via a generic Platform adapter interface,
            src/contentmaster/platforms/, so other platforms plug in without
            touching pipeline.py — see README.md "Platforms")
        │
        ▼
   queue for review — no metrics pull here at all. Writing the entry
        (plays/_tracking_review.jsonl) *is* the "confirmed it really
        published" record. This is where `contentmaster run` stops.
        │
        │   ...real time actually passes...
        │
        ▼
   `contentmaster review` — a separate, later-triggered command (manual,
        or the user's own launchd/cron for now) that finds posts due for
        one of 3 checkpoints and, for each, pulls real metrics for the
        *first* time since publish:
          24h  — did the algorithm even push this out?
          7d   — is the heat sustained? (the "golden verdict" tier)
          30d  — long-term/monthly rollup (not yet built as a distinct
                 analysis shape — see docs/SCHEDULE.md)
        Every comparison below stays within one tier — a 24h reading is
        never compared against a 7d one, that would compare different
        things.
        │
        ▼
   analysis: cross-validate against this product/channel's real history
             *at this same checkpoint tier* in the warehouse (DuckDB, via
             dbt) — was the last suggestion actually followed by better
             numbers, what's the trend — then a human confirms or
             overrides the verdict (src/contentmaster/analysis.py; small
             sample sizes mean this stays human-checked, not blindly
             trusted)
        │
        ▼
   discuss: 2 different local models each propose a next-round strategy
            grounded in that analysis, a 3rd synthesizes them into one —
            real model diversity, not one model guessing from N=1
            (src/contentmaster/discuss.py)
        │
        ▼
   log: this checkpoint's result + the analysis + the synthesized
        strategy, written to memory, tagged with which checkpoint tier
        produced it ──────────────────────────────────────┐
        │                                                    │
        ▼                                                    │
   next round's generation reads back the *highest-maturity* completed
   checkpoint available (prefer a 7d reading over a 24h one, even from an
   older post, not just whichever is most recent) ◀────────────┘
```

This is a real, running loop today (text-only, Bluesky-only) — not just a
diagram. See `src/contentmaster/pipeline.py` (`run()` for the top half,
`run_review()` for the bottom half) and `docs/LOG.md` 2026-09-15 for the
full design discussion behind the split.

## 3. Architecture

| Layer | Tool | Why |
|---|---|---|
| Extraction | **Cognee** (self-hosted, `docling`/`unstructured` extras for non-PDF formats) + **faster-whisper** for audio/podcast | Already open-source; the ECL (Extract-Cognify-Load) pipeline is genuinely suited to building a graph that compounds across many inputs over time — the thing this project actually needs once it's not a single-document demo |
| Text generation | **Ollama**, local | Free, private, swappable — the same interface can point at a hosted model (Claude/GPT via a thin adapter) when quality matters more than staying fully offline |
| Image generation | **FLUX.1-schnell via Cloudflare Workers AI** (done 2026-09-16) — not run locally as originally planned; this machine's 8GB RAM is too tight to share with Cognee + Ollama for a local diffusion pipeline | Same open-weight model originally planned, just hosted. Free tier (10,000 Neurons/day, ~173 1024x1024 images/day, resets daily, no expiry) means ~$0 ongoing cost at this project's real volume — see `docs/LOG.md` 2026-09-16 for the comparison against Replicate/fal.ai/Stability/OpenAI |
| Video | Scripted composition (LLM script → generated images → local TTS → `ffmpeg`/`moviepy`), not a generative video model | Open generative video models aren't yet good/fast enough for real marketing output; composition is a more honest use of current tools |
| Human review | **Streamlit** or **Gradio** | A terminal `input()` prompt can't show an image or a video preview; this can |
| Publishing | **Bluesky** (done), behind a generic `Platform` adapter interface (`platforms/base.py`) — **Mastodon** next | Both free, open protocols — no vendor account/API-approval bottleneck; the adapter interface means new platforms (X, Instagram, Facebook, YouTube) plug in without touching orchestration code |
| Analytics | **DuckDB**, transformed via **dbt**, optionally shared via **MotherDuck** — today also ingests Google Trends CSVs (`warehouse/seeds/`) for real cross-validation against external signal, and every row is tagged with which checkpoint tier (24h/7d/30d) produced it | Embedded, real SQL, zero accounts for local use; MotherDuck is a one-flag upgrade to a shared warehouse when needed. See `warehouse/README.md` |
| Memory / dataset | **LanceDB** (planned) — today: flat JSON/JSONL (`plays/_history.jsonl` the real timeline, `plays/*.json` current-state snapshots, `plays/{product}_topics.json` the topic index) | Portable, embeddable, and — critically — model-agnostic: the whole point is that any LLM can read this, not just whichever one wrote it. The flat-file version today already gives real cross-run memory; LanceDB is for when semantic retrieval over that history actually earns its keep (needs real data volume first — see docs/SCHEDULE.md) |
| Distribution | A thin, self-built **MCP server** over the LanceDB dataset (planned) | Lets any MCP-capable LLM client query "what's worked before" without retraining anything |
| Orchestration | `src/contentmaster/pipeline.py` (hand-rolled) — `run()` (extract → draft → review → publish → queue) and `run_review()` (checkpoint → analyze → discuss → log), triggered separately as `contentmaster run` / `contentmaster review` | Already does the real work; a heavier framework (LangGraph) only earns its keep if/when the control flow gets a lot more complex |

**Deliberately not used going forward:** RocketRide.ai (Cloud UI friction,
and the real pipeline never actually depended on it — see
`docs/ERROR_LOG.md`), HydraDB (**removed from `pipeline.py` 2026-09-13** —
superseded by LanceDB for memory; the project's own graph-relationship
needs don't require a separate graph DB), hotdata.dev (**removed
2026-09-13** — superseded by DuckDB/dbt), Snyk stays (kept deliberately —
security scanning has standalone value regardless of this pivot).

## 4. Model improvement, longer-term

Retrieval (LanceDB) is the memory mechanism now — it's mature, cheap,
transparent, and model-agnostic. Once the dataset is large enough and
consistently high-quality, the plan is periodic (not continuous) fine-tuning
of a small open-weight model via DPO or GRPO — evaluated and versioned each
time, not updated online. True continual/online weight updates (e.g.
continual backprop) were considered and deliberately deferred: the
technique is unproven at LLM scale, and baking learning into one model's
weights conflicts directly with the "any LLM can use this" distribution
goal. See `docs/LOG.md` (2026-09-12) for the full reasoning.

PyTorch/LoRA infrastructure is intentionally not built yet — there isn't
enough data for it to matter, and building it now would be speculative
work.
