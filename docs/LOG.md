# Log

Dated, append-only record of what actually happened. Newest entries at the
bottom. This is history, not a plan — see `docs/SCHEDULE.md` for what's
next. Errors worth remembering go in `docs/ERROR_LOG.md` instead, with a
cross-reference back here.

## 2026-09-11 — Hackathon build

- Built the full loop against the five mandated sponsor tools: Cognee
  (self-hosted, wired to local Ollama after the LLM-key and endpoint
  issues in `docs/ERROR_LOG.md`), HydraDB (local graph-node + a real
  HydraDB Cloud database for the RocketRide `.pipe`), hotdata.dev (real
  workspace/database/table via the CLI), RocketRide.ai (SDK-verified
  connection; a real `.pipe` hand-authored against the VS Code extension's
  own schema docs, not built via the Cloud canvas — see
  `docs/ERROR_LOG.md`), Modiqo.ai/Rote (hello-world play passed), Snyk
  (dependency + code scan clean).
- Added Bluesky as a real data source (fetch) and, later the same day, a
  real publish target — `client.post()` gated behind the existing human
  review step.
- Found and fixed three real bugs in the core loop (not sponsor-tool
  issues): Cognee's extraction result was silently discarded so drafts
  never actually used the uploaded document; `.rtf` files 500'd against
  Cognee; every run shared one Cognee dataset so unrelated content bled
  between runs. All three in `docs/ERROR_LOG.md`.
- Closed the "read metrics → improve → write a genuinely better post"
  loop: repeat runs now feed the prior post + its real metrics + an
  LLM-generated improvement note back into generation, instead of either
  a cold start or a frozen verbatim replay.
- Confirmed nothing in the working pipeline was costing money or tokens
  against any vendor: all LLM calls route through local Ollama, Cognee and
  HydraDB (local) are self-hosted, and the cloud accounts (RocketRide,
  HydraDB Cloud, hotdata) were all still on free tiers.

## 2026-09-12 — Architecture pivot: hackathon submission → open-source project

- Hackathon over; decided to keep developing this independently rather
  than freeze it as a submission. Re-evaluated every sponsor tool with the
  hackathon constraint removed.
- Removed sponsor-required local installs that added no ongoing value:
  Trae.app, the `rote` CLI + its local data, the `hotdata` CLI (brew),
  npm's `snyk` (briefly — see below), the Cursor RocketRide extension, the
  `cognee`/`hydradb` Docker containers and images, and the unused
  `llama3.2:1b` Ollama model.
- Corrected scope right after: Cognee and Snyk were meant to stay (Cognee
  because the new architecture still uses it; Snyk because security
  scanning has independent value). Reinstalled both — Snyk's auth token
  had survived the npm uninstall so no re-login was needed; Cognee's
  Docker container and user account had to be recreated from scratch
  (the `cognee-data` volume was gone, so the ingested cat.rtf knowledge
  graph from the hackathon didn't survive — re-verified add → cognify →
  search all work on the fresh container).
- Landed on a target architecture with the hackathon constraint removed:
  Cognee (kept) + Ollama + FLUX/SDXL (image) + script-based video + Bluesky
  + Mastodon + DuckDB/dbt/MotherDuck (analytics) + LanceDB (memory) + a
  self-built MCP server (distribution) + the existing hand-rolled
  orchestrator. RocketRide, HydraDB, and hotdata.dev are not part of this
  going forward (see `docs/WHITEPAPER.md` §3 for the reasoning per tool).
- Deliberately deferred model weight-level learning: compared LanceDB
  retrieval against continual backpropagation for the memory layer.
  Continual backprop is unproven at LLM scale, is far more resource-
  intensive than this project needs today, and — the deciding factor —
  ties learning to one specific model's weights, which conflicts with the
  stated goal of a dataset any LLM can plug into. Landed on: retrieval now
  (LanceDB), periodic (not continuous) DPO/GRPO fine-tuning of a small
  open-weight model once the dataset is large and clean enough, evaluated
  and versioned each time rather than updated online.
- Set up `warehouse/`: a dbt project on `dbt-duckdb`, with a `dev` target
  (local `.duckdb` file, no account) and a `cloud` target (MotherDuck, one
  flag away). First real data ran through it end to end: `plays/*.json` +
  `plays/_failures.jsonl` + `audit/events.jsonl` → `campaign_performance`,
  a unified published/rejected table with real metrics — chosen over
  keeping hotdata.dev because it needs zero account for local use, is the
  same class of embedded SQL engine hotdata itself is built on
  (DataFusion), and has a documented one-command upgrade path (MotherDuck)
  for when this needs to be shared.
- Wrote this log, `docs/SCHEDULE.md`, and `docs/ERROR_LOG.md` — the
  explicit ask being to stop re-discovering the same fixes and to keep
  intent (schedule) separate from what actually happened (this file).
