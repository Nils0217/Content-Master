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
gets published. The agent then tracks real performance against
human-defined KPIs, crocess analysis performance and enviromental conditional, proposes a concrete improvement (or confirms the approach worked), and writes the result into a durable memory layer. The next round of content generation draws on that memory — not a cold start,
not a frozen replay, a version that acted on what was actually learned.

The project is open source, CLI design (`contentmaster` — see `README.md`).
As the dataset of (content → post → real
performance → improvement) accumulates, the goal is to package it as a
domain-specific plugin any LLM can attach to — via MCP — so the knowledge
compounds independently of which model is doing the writing.

## 2. The loop

```
any input (doc / video / code / podcast)
        │
        ▼
   extraction (Cognee)
        │
        ▼
   content generation (local LLM, grounded in the extraction)
        │
        ▼
   human review  ──reject──▶  logged as a negative example
        │ approve
        ▼
   publish (Bluesky as testing — via a generic Platform adapter interface,
            src/contentmaster/platforms/, so other platforms plug in without
            touching pipeline.py — see README.md "Platforms")
        │
        ▼
   pull real results (likes / reposts / replies against a human-set KPI)
        │
        ▼
   log (DuckDB, via dbt)
        │
        ▼
   propose improvement (local LLM, given the real result)
        │
        ▼
   write to memory ──────────────────────────────────┐
        │                                             │
        ▼                                             │
   next round's generation reads this memory ◀────────┘
```

This is a real, running loop today (text-only, Bluesky-only) — not just a
diagram. See `src/contentmaster/pipeline.py`.

## 3. Architecture

| Layer | Tool | Why |
|---|---|---|
| Extraction | **Cognee** (self-hosted, `docling`/`unstructured` extras for non-PDF formats) + **faster-whisper** for audio/podcast | Already open-source; the ECL (Extract-Cognify-Load) pipeline is genuinely suited to building a graph that compounds across many inputs over time — the thing this project actually needs once it's not a single-document demo |
| Text generation | **Ollama**, local | Free, private, swappable — the same interface can point at a hosted model (Claude/GPT via a thin adapter) when quality matters more than staying fully offline |
| Image generation | **FLUX.1 schnell** / **SDXL Turbo**, local | Open-weight, fast enough to iterate on a single Mac |
| Video | Scripted composition (LLM script → generated images → local TTS → `ffmpeg`/`moviepy`), not a generative video model | Open generative video models aren't yet good/fast enough for real marketing output; composition is a more honest use of current tools |
| Human review | **Streamlit** or **Gradio** | A terminal `input()` prompt can't show an image or a video preview; this can |
| Publishing | **Bluesky** (done), behind a generic `Platform` adapter interface (`platforms/base.py`) — **Mastodon** next | Both free, open protocols — no vendor account/API-approval bottleneck; the adapter interface means new platforms (X, Instagram, Facebook, YouTube) plug in without touching orchestration code |
| Analytics | **DuckDB**, transformed via **dbt**, optionally shared via **MotherDuck** | Embedded, real SQL, zero accounts for local use; MotherDuck is a one-flag upgrade to a shared warehouse when needed. See `warehouse/README.md` |
| Memory / dataset | **LanceDB** | Portable, embeddable, and — critically — model-agnostic: the whole point is that any LLM can read this, not just whichever one wrote it |
| Distribution | A thin, self-built **MCP server** over the LanceDB dataset | Lets any MCP-capable LLM client query "what's worked before" without retraining anything |
| Orchestration | `src/contentmaster/pipeline.py` (hand-rolled) | Already does the real work; a heavier framework (LangGraph) only earns its keep if/when the control flow gets a lot more complex |

**Deliberately not used going forward:** RocketRide.ai (Cloud UI friction,
and the real pipeline never actually depended on it — see
`docs/ERROR_LOG.md`), HydraDB (superseded by LanceDB for memory; the
project's own graph-relationship needs don't require a separate graph DB),
hotdata.dev (superseded by DuckDB/dbt), Snyk stays (kept deliberately —
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
