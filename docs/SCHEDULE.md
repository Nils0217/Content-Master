# Schedule

Task backlog, roughly ordered. No dates on purpose — see `docs/LOG.md` for
what's actually been done and when. Check items off in place; don't delete
finished ones (keeps this file useful as a record of intent vs. reality).

## Phase 0 — retire hackathon-only dependencies

- [ ] Remove `step2_hydradb_persist()` from `pipeline.py` (HydraDB is
      superseded by LanceDB — see `docs/WHITEPAPER.md` §3). Don't just
      delete it silently: replace it with a real write to wherever memory
      lands (LanceDB, once Phase 2 exists), so the loop stays complete.
- [ ] Replace `hotdata_client.py`'s role with the new `warehouse/` dbt
      project — `step6_hotdata_metrics()` should write to DuckDB directly
      (or via a thin wrapper) instead of shelling out to the `hotdata` CLI.
- [ ] Decide what to do with `pipelines/automarketer-content-gen.pipe` and
      `rocketride_client.py` — the real pipeline never depended on
      RocketRide's execution, only the demo `.pipe` artifact did. Keep as
      a historical artifact, or remove.

## Phase 1 — data layer (dbt + DuckDB [+ MotherDuck])

- [x] `warehouse/` dbt project scaffolded, `dev` (local DuckDB) and
      `cloud` (MotherDuck) targets
- [x] Staging models for `plays/*.json`, `plays/_failures.jsonl`,
      `audit/events.jsonl`
- [x] `campaign_performance` mart (published + rejected, unioned)
- [ ] Add dbt tests (not-null, accepted-values on `status`, etc.)
- [ ] Seed one real external CSV (news/current-events data) and join it
      against `campaign_performance` in a model — the first real use of
      `warehouse/seeds/`
- [ ] Decide the MotherDuck cutover trigger (data volume? multiple
      contributors? just do it once curious) and actually do it once
      reached

## Phase 2 — memory layer (LanceDB)

- [ ] Replace `modiqo_play.py`'s flat `plays/*.json` files with a LanceDB
      table: same fields (product, channel, winning_text, metrics,
      improvement_note), but embeddable/queryable by similarity, not just
      exact (product, channel) key lookup
- [ ] `rocketride_client.draft_posts()`'s `prior` argument should become a
      LanceDB similarity query ("find the most relevant past result for
      this product/channel/topic"), not just an exact-match play lookup
- [ ] Decide on an embedding model (local, via Ollama's embedding support
      or a small sentence-transformer) for the LanceDB vectors

## Phase 3 — multi-modal ingestion

- [ ] Install `cognee[docling]` or `cognee[unstructured]` on the Cognee
      container so non-PDF/txt/md formats stop 500ing (see
      `docs/ERROR_LOG.md` — `.rtf` already hit this)
- [ ] `faster-whisper` for audio/podcast transcription → feed the
      transcript into Cognee the same way `add_raw_texts()` does for
      Bluesky posts today
- [ ] Code ingestion: either lean on Cognee's own code-graph support, or
      add a `tree-sitter`-based structural extractor

## Phase 4 — multi-modal generation

- [ ] Image generation: FLUX.1 schnell or SDXL Turbo, local
- [ ] Video: LLM-written script → generated images → local TTS (Kokoro or
      Piper) → `ffmpeg`/`moviepy` composition. Not a generative video model
      (see `docs/WHITEPAPER.md` §3 for why)

## Phase 5 — human review UI

- [ ] Replace `human_loop.py`'s CLI `input()` flow with a Streamlit or
      Gradio app — needed before image/video review is usable at all
- [ ] Carry over the existing interface (`ReviewDecision` in/out) so
      `pipeline.py` doesn't need to change, only `human_loop.py`'s
      internals

## Phase 6 — channels

- [ ] Mastodon adapter, mirroring `bluesky_client.py`'s shape
      (`test_connection`, `fetch_public_posts`, `publish_post`,
      `get_post_metrics`)

## Phase 7 — distribution (MCP server)

- [ ] Thin MCP server exposing the LanceDB dataset: at minimum
      `recall_winning_pattern(industry, channel, goal)` and
      `log_campaign_result(...)`
- [ ] Publish it so any MCP-capable client (Claude Desktop, Claude Code,
      etc.) can attach to it without touching this repo's code

## Phase 8 — model improvement (deferred, don't start early)

- [ ] Once `campaign_performance` has enough high-quality rows: prepare a
      DPO-format dataset (winning vs. rejected pairs already exist in the
      mart — this is mostly a formatting step)
- [ ] Pick a small open-weight base model (Qwen2.5 or Llama 3.2 family)
- [ ] Verify the ROCm toolchain actually works end-to-end on the AMD
      machine with a trivial fine-tune *before* committing real data prep
      time to this phase
- [ ] Periodic (e.g. monthly) batch fine-tune, evaluated against the
      previous version before it replaces it — never online/continuous
