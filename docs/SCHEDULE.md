# Schedule

Task backlog, roughly ordered. No dates on purpose — see `docs/LOG.md` for
what's actually been done and when. Check items off in place; don't delete
finished ones (keeps this file useful as a record of intent vs. reality).

## Phase 0a — packaging

- [x] `pyproject.toml` + `src/contentmaster/cli.py`: `contentmaster` is now
      a real installed command (`pip install -e .`), with `run`,
      `platforms`, and `connect <platform>` subcommands. `run_pipeline.py` /
      `scripts/verify_bluesky.py` kept as deprecated forwarding wrappers.
- [x] Project renamed `automarketer` → `contentmaster` (package dir, CLI
      command, dbt project/profile name, Cognee default dataset name).
      `src/automarketer/` left on disk as dead code — this dev
      environment's `rm` was blocked when the rename happened; delete by
      hand (`rm -rf src/automarketer`). hotdata.dev's `automarketer`
      catalog name deliberately NOT renamed — it's a real external
      resource, not the project's own name (see `hotdata_client.py`).
- [x] Bluesky pulled out from under a bluesky-specific CLI subcommand into
      a generic `Platform` adapter interface (`src/contentmaster/platforms/`)
      — `contentmaster connect <platform>` and `run --channel <platform>`
      work with whatever's registered, not just Bluesky. Mastodon, X,
      Instagram, Facebook, YouTube are named as planned placeholders in
      `platforms/registry.py` (not implemented) — see Phase 6.
- [ ] As Phase 1–7 land, add their subcommands here too instead of new
      standalone scripts (e.g. `contentmaster warehouse build`, once the
      dbt invocation is worth wrapping).

## Phase 0 — retire hackathon-only dependencies

- [x] Remove `step2_hydradb_persist()` from `pipeline.py`. It had been
      silently crashing every run (the local HydraDB Docker container was
      deleted in an earlier cleanup pass, so the call 500'd on connection
      refused) — this was the actual reason `contentmaster run` couldn't
      complete. Also dropped the redundant `HAS_PLAY` graph write in
      `step7_modiqo_capture` (the real source of truth was already
      `capture_success()` writing to `plays/*.json` +
      `plays/_history.jsonl`, so nothing was lost). `hydradb_client.py`
      itself and the local `hydradb-data/` are untouched, just no longer
      imported by `pipeline.py` — same dead-code treatment as
      `src/automarketer/`/`hotdata_client.py` (`rm` blocked in this dev
      environment; delete by hand: `rm -f src/contentmaster/hydradb_client.py`).
      No LanceDB replacement written yet (Phase 2 isn't built) — the
      product/feature/ICP graph this step used to persist just isn't
      recorded anywhere right now, which is the honest state until Phase 2
      lands, not a silent regression.
- [x] Replaced `hotdata_client.py`'s role with the new `warehouse/` dbt
      project — `step6_track_metrics()` (renamed from
      `step6_hotdata_metrics()`) writes to `metrics/post_metrics.jsonl` via
      `metrics_store.py`, and `stg_post_metrics.sql` reads it into DuckDB.
      No CLI, no account. `hotdata_client.py` itself untouched but
      unimported — same dead-code treatment, delete by hand:
      `rm -f src/contentmaster/hotdata_client.py`.
- [x] `rocketride_client.py` renamed `draft_generator.py`
      (`RocketRide` class → `DraftGenerator`): it never depended on
      RocketRide's execution — `draft_posts()` always called the local
      Ollama LLM directly; `run_pipe()`/`account_info()` (the only methods
      that touched the actual RocketRide SDK) weren't called anywhere and
      were dropped along with the rename. `pipeline.py`'s
      `step3_rocketride_or_replay` → `step3_generate_drafts`, audit event
      stage `"rocketride"` → `"draft_generator"`. Verified live: a real
      run still generates grounded drafts, logs `draft_generator/
      draft.start` and `.done`. `rocketride_client.py` itself left as dead
      code (unimported) — delete by hand:
      `rm -f src/contentmaster/rocketride_client.py`.
- [ ] `pipelines/automarketer-content-gen.pipe` itself: keep as historical
      artifact, or remove.

## Phase 1 — data layer (dbt + DuckDB [+ MotherDuck])

- [x] `warehouse/` dbt project scaffolded, `dev` (local DuckDB) and
      `cloud` (MotherDuck) targets
- [x] Staging models for `plays/*.json`, `plays/_failures.jsonl`,
      `audit/events.jsonl`
- [x] `campaign_performance` mart (published + rejected, unioned) —
      current-state snapshot, at most 1 published row per product/channel
- [x] `performance_history` mart + `stg_success_history` (reads new
      `plays/_history.jsonl`, an append-only ledger — see Phase 9) — the
      real timeline `analysis.py` cross-validates against; unlike
      `campaign_performance`, this actually accumulates over multiple runs
- [ ] Add dbt tests (not-null, accepted-values on `status`, etc.)
- [ ] Seed one real external CSV (news/current-events data) and join it
      against `performance_history` in a model — the first real use of
      `warehouse/seeds/`, and the "cross validate against other data" half
      of Phase 9's analysis step that isn't built yet (only the
      within-product-history half is)
- [ ] Decide the MotherDuck cutover trigger (data volume? multiple
      contributors? just do it once curious) and actually do it once
      reached

## Phase 2 — memory layer (LanceDB)

- [ ] Replace `modiqo_play.py`'s flat `plays/*.json` files with a LanceDB
      table: same fields (product, channel, winning_text, metrics,
      improvement_note), but embeddable/queryable by similarity, not just
      exact (product, channel) key lookup
- [ ] `draft_generator.draft_posts()`'s `prior` argument should become a
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

- [ ] Mastodon adapter: `platforms/mastodon.py` implementing `Platform`
      (`platforms/base.py`), registered in `platforms/registry.py`'s
      `PLATFORMS` (move it out of `PLANNED_PLATFORMS`). Mirror
      `platforms/bluesky.py`'s shape.
- [ ] Same for X, Instagram, Facebook, YouTube as each becomes worth
      building — no CLI or `pipeline.py` change needed per platform, just
      the adapter file + registry entry.

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

## Phase 9 — analysis & discuss (track -> analysis -> discuss -> log)

- [x] `plays/_history.jsonl`: append-only ledger of every successful
      capture (see `modiqo_play._append_history()`), backfilled from the
      pre-existing play snapshots. Without this, "did the last suggestion
      help" and "what's the trend" were both unanswerable —
      `plays/*.json` alone is a snapshot with at most 1 data point per
      product/channel, ever.
- [x] `warehouse/models/staging/stg_success_history.sql` +
      `models/marts/performance_history.sql` — the real timeline mart
      built from that ledger (+ `stg_failures`), which `analysis.py`
      queries.
- [x] `src/contentmaster/analysis.py`: cross-validates the current run's
      ctr against this product/channel's real history (n_prior_posts,
      historical_avg_ctr, trend — labeled `insufficient_for_trend` below
      n=2 rather than a fabricated verdict from a tiny sample) and whether
      the *previous* improvement_note's suggestion looks like it
      correlated with ctr moving. Human-in-the-loop by default
      (`interactive=True`) — prints the verdict, lets a human confirm or
      override before `discuss.py` trusts it.
- [x] `src/contentmaster/discuss.py`: 2 different local models
      (llama3.2:3b + mistral:latest) each independently propose a
      next-round strategy grounded in the analysis; a 3rd pass
      synthesizes them into one instruction. One bounded round —
      deliberately not an open-ended multi-turn debate (cost/latency
      multiply for real, audit trail gets harder to follow, and
      same-model self-debate doesn't add real cross-validation anyway).
- [x] `pipeline.py` wired: `step7_modiqo_capture` now calls
      `step7a_analyze_and_discuss` instead of the old single-model
      `step7b_llm_improve` (removed); the synthesized strategy is stored
      under the existing `improvement_note` key (so
      `rocketride_client.py`'s prompt-building needed no change) plus a
      new `analysis` dict alongside it, on both the snapshot and the
      history ledger.
- [ ] **Cross-validate against *other* data, not just this
      product/channel's own history** — the user's original ask included
      this ("gather 成效, other data in the motherDB, and cross validate
      them"); only the within-product-history half is built. Needs real
      external data seeded into the warehouse first (see Phase 1's seeds
      item) before there's anything to cross-validate against.
- [ ] `analyze_performance(interactive=...)` / `synthesize_strategy(...)`
      have no CLI-level way to run non-interactively yet (e.g. for a
      scripted/batch mode) — `interactive=False` works as a function
      argument, but nothing in `cli.py`/`pipeline.run()` exposes a flag
      for it.
- [ ] `discuss.py`'s judge defaults to the same model as "Advisor A"
      (`DISCUSS_JUDGE_MODEL` defaults to `DISCUSS_MODEL_A`) purely because
      it's the cheaper/faster of the two locally available models — worth
      revisiting once a 3rd genuinely different model is available
      locally, so the judge isn't one of the two it's arbitrating between.
- [ ] No dbt tests on `performance_history` yet (see Phase 1's dbt-tests
      item — applies here too).
