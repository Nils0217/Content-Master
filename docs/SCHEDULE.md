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
- [x] Add dbt tests (not-null, accepted-values on `status`, etc.). **Done
      (2026-09-15)**: `models/staging/_staging__models.yml` +
      `models/marts/_marts__models.yml`, 33 tests total, 29 pass. The 4
      that don't are real/known, not test bugs — see docs/LOG.md.
- [x] Seed one real external CSV (news/current-events data) and join it
      against `performance_history` in a model — the first real use of
      `warehouse/seeds/`. **Done (2026-09-14)**: 4 Google Trends "cat"
      CSVs cleaned via DuckDB SQL and seeded — `webSearch_cat_timeline`
      (daily search interest, the only one with a per-day grain) is
      LEFT JOINed into `performance_history` as
      `external_cat_search_interest`; verified live against real rows in
      `plays/_history.jsonl` (all matched, non-null). The other 3
      (`google_trends_cat_related_queries_top/rising`,
      `webSearchcat_region_clean`) aren't row-joinable — kept as
      standalone staging reference tables instead
      (`stg_google_trends_related_top/rising`, `stg_google_trends_region`)
      for the draft/discuss prompt to pull from later, not for the
      `performance_history` join. See docs/LOG.md.
- [x] `analysis.py`/`discuss.py` wired to the new data. **Done
      (2026-09-14)**: `AnalysisResult` gained `external_signal` (current
      search interest + a hedged high/low-interest-day ctr comparison,
      n-aware) and `trending_context` (top/rising related queries + top
      regions from the 3 reference tables), both fed into
      `discuss.py`'s proposal prompt. Verified live against real Ollama
      models — both advisors picked up the trending term "cat in the hat"
      unprompted and worked it into their proposals, judge synthesized a
      strategy naming it explicitly. See docs/LOG.md.
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

- [x] **Image generation — done 2026-09-16.** Originally planned as
      "FLUX.1 schnell or SDXL Turbo, local" — changed to **hosted**
      after checking this machine's actual specs (8GB RAM, Apple
      Silicon): a local FLUX/SDXL pipeline realistically wouldn't run
      well here, alongside Cognee + Ollama already competing for the
      same memory. Compared Replicate / fal.ai / Stability API / OpenAI
      / Cloudflare Workers AI (see docs/LOG.md for the full comparison);
      picked **Cloudflare Workers AI running FLUX.1-schnell** — same
      model originally planned, just hosted, and its free tier
      (10,000 Neurons/day, a 1024x1024 image = 57.6 Neurons, ~173
      free images/day, resets daily, no expiry) realistically means
      $0 ongoing cost at this project's volume, unlike Replicate/fal.ai
      which charge from image #1.
      Built: new `src/contentmaster/image_generator.py`
      (`generate_image()`, silently returns `None` if unconfigured or
      the call fails — never blocks a text-only publish); new
      `CloudflareSettings` in `config.py` + `CLOUDFLARE_ACCOUNT_ID`/
      `CLOUDFLARE_API_TOKEN` in `.env`/`env.example` (left empty — real
      credentials are the user's to add, never filled in here);
      `platforms/base.py`'s `Platform.publish_post()` gained optional
      `image`/`image_alt` params (a platform with no image support can
      just ignore them); `platforms/bluesky.py` implements it via
      atproto's `Client.send_image()`. New `human_loop.review_image()` —
      same brand-safety principle as `review_draft()`/the `[f]eedback`
      fix: nothing generated gets attached to a real post without a
      human looking at it first. Since a CLI can't preview an image
      (that's Phase 5's whole reason for existing), it saves to a temp
      file, prints the path, and offers `[a]ttach`/`[r]egenerate`/
      `[s]kip (text-only)`. Wired into `pipeline.run()` right after text
      approval (grounds the image prompt in the *final* approved text)
      and before publish. Verified: generation degrades to `(None, "")`
      cleanly with no Cloudflare credentials configured; `review_image()`'s
      full loop (invalid input errors, regenerate produces new bytes,
      attach returns them) tested with a mocked regenerate call. **Real
      Cloudflare API call verified 2026-09-16 (later)**: once the user
      added their own account credentials, `generate_image()` returned a
      real 494KB PNG, visually confirmed coherent and on-prompt — not
      mocked. Still not verified: an image actually attached to a real
      Bluesky post end to end (the generation half is now proven live,
      the publish-with-image half hasn't been exercised yet).
- [ ] Video: LLM-written script → generated images → local TTS (Kokoro or
      Piper) → `ffmpeg`/`moviepy` composition. Not a generative video model
      (see `docs/WHITEPAPER.md` §3 for why)

## Phase 5 — human review UI

- [x] **Design decided via `/grill-me` (2026-09-17), MVP built and
      verified live, then real queue-based integration built
      (2026-09-18, see docs/LOG.md for both).** Corrects an earlier
      wrong assumption in this file: "pipeline.py doesn't need to
      change" was **not** right. Streamlit can't block on `input()` the
      way the CLI does, so `pipeline.run()` genuinely needed
      restructuring (generation decoupled from review via a queue
      file), not just a swap inside `human_loop.py`.
      Decided: (1) draft + image generated together, eagerly, not the
      earlier 2-stage CLI timing. (2) Only the `input()`-calling
      functions in `human_loop.py` are CLI-specific. `ReviewDecision`
      and `draft_generator.revise_post()` are shared, not duplicated,
      between the CLI and Streamlit paths. (3) Approve = publish
      immediately, no second confirm step (explicit user call, overrides
      the two-step-safety default used elsewhere this session). A
      scheduled-publish variant (approve = schedule, not post now) was
      raised as a real future option, marked TBD. (4) One shared
      generation code path, review interface as the only variable
      (`terminal: bool` flag on `pipeline.run()`, `--terminal` on the
      CLI), not two separate generation implementations. Default
      flipped from the earlier draft: no flags now opens the browser,
      `--terminal` opts into the CLI review flow instead.
      **MVP built and verified live first** (see docs/LOG.md
      2026-09-17): a single-page prototype that generated a draft +
      image on a button click, proved `revise_post()` works through
      Streamlit's button-driven flow in a real Chrome tab.
      **Then replaced with the real integration** (2026-09-18): new
      `draft_queue.py` (`plays/_draft_review.jsonl`, append-only, last
      write wins, same pattern as `tracking_review.py`); `pipeline.py`
      split into `_extract_topics_and_generate()` (shared),
      `_run_terminal()` (original flow, unchanged), and
      `_run_streamlit()` (generates every draft's image eagerly, queues
      it, calls `launch_streamlit()`, which opens Streamlit without
      `--server.headless` so it auto opens the browser on its own).
      `streamlit_app.py` rewritten to read `draft_queue.find_pending()`
      instead of generating on the page, with Approve wired to the real
      `step5_publish()`/`step6_queue_for_review()`, Edit to
      `session_state`, Send feedback to the same `revise_post()` call,
      Reject to `modiqo_play.capture_failure()`, each action also
      updating the queue entry's status. `streamlit` stays an optional
      dependency (`pip install -e ".[review-ui]"`), not core.
      Verified: `pipeline.py`/`cli.py`/`draft_queue.py` import cleanly,
      `contentmaster run --help` shows `--terminal`, `streamlit_app.py`
      compiles and every function it calls matches the real signatures
      it depends on, and a disposable test entry proved the queue
      lifecycle end to end. **Not yet verified**: an actual browser
      session against the new queue-based page (held off since the
      background Streamlit process from the MVP testing had just been
      killed for low memory).
- [x] **Extended to `contentmaster review`'s human confirmation step
      (2026-09-18, `/grill-me`, see docs/LOG.md).**
      `analysis.confirm_with_human()` had the exact same blocking-`input()`
      problem as the old draft review — same fix, mirroring the pattern
      above rather than reinventing it: new `analysis_queue.py`
      (`plays/_analysis_review.jsonl`, same append-only/last-write-wins
      pattern, but keyed on `(post_id, checkpoint)` since one post can
      have three separate pending analyses over its life, not `draft_id`
      alone). `pipeline.py`'s `step_checkpoint_analyze()` split the same
      way `run()` was: `_pull_and_analyze()` (shared eager metrics-pull +
      analyze + synthesize), `_run_review_terminal()` (original flow,
      unchanged), `_run_review_streamlit()` (queues instead of blocking).
      Capture (`capture_success`/`capture_failure`, still decided purely
      by the real ctr threshold, independent of the human's
      confirm/disagree choice) and `tracking_review.mark_checkpoint_done()`
      both moved to decision time — a new `apply_analysis_decision()`,
      called by `_run_review_terminal()` right after its own
      `confirm_with_human()` and by Streamlit's Confirm/Disagree buttons.
      `run_review()` gained a `terminal: bool` flag (`--terminal` on the
      CLI, same semantics as `run --terminal`) and a dedup check: a due
      post already pending a decision in `analysis_queue` is skipped by
      *either* mode, so a still-undecided browser item can't get
      re-analyzed or double-captured by a second invocation.
      `streamlit_app.py` gained an `st.tabs()` split ("Draft review" /
      "Analysis review") on the *same* page/process rather than a second
      app — decided via `/grill-me` specifically to avoid two Streamlit
      processes fighting over the same port. `launch_streamlit()` (shared
      by both `run()` and `run_review()`) now probes `localhost:8501`
      with a plain socket connect before launching, and skips the launch
      if something's already listening — closes a real gap along the way
      too: the old unconditional `Popen` had `stderr=DEVNULL`, so a
      failed second launch used to fail completely silently.
      Verified: `pipeline.py`/`cli.py`/`analysis_queue.py`/
      `streamlit_app.py` import and compile cleanly,
      `contentmaster review --help` shows `--terminal`, a disposable
      `(post_id, checkpoint)` entry proved `analysis_queue`'s full
      lifecycle (queue -> pending -> dedup-checked -> decided -> no
      longer pending), and `pipeline.apply_analysis_decision()` was
      exercised for real against that disposable entry — audit events
      fired, `capture_failure()` wrote to both `_failures.jsonl` and
      `_history.jsonl` (ctr below threshold), and
      `tracking_review.mark_checkpoint_done()` recorded the checkpoint.
      The cross-mode dedup check's setup accidentally triggered a real,
      already-due post's real Bluesky metrics pull and real local-LLM
      analysis mid-test (it was first in iteration order) before hitting
      `ReviewInterrupted` on the non-interactive `input()` — confirmed
      this left no partial/incorrect state (no capture or
      `mark_checkpoint_done` happens before that point), so the real post
      is still cleanly due for its next real `contentmaster review` run.
      **Not yet verified**: an actual browser session against the new
      "Analysis review" tab — held off this time because the machine was
      down to ~73MB free physical memory (same class of constraint that
      killed the earlier Streamlit MVP test process, see the entry
      above), not worth risking now.
- [ ] Still open: handling `topic_index.py`'s interactive topic-review
      step for a brand-new/changed whitepaper when defaulting to the
      browser (currently punted, same as the MVP: use `--terminal` for
      that part first). A full end to end browser test of the real
      queue-based draft flow, and now also of the new Analysis review tab.

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
- [x] **Cross-validate against *other* data, not just this
      product/channel's own history** — done 2026-09-14, see Phase 1's
      seeds item (`external_signal` + `trending_context` on
      `AnalysisResult`, wired into `discuss.py`'s prompt).
- [x] **Checkpoint failures now feed forward too, not just successes**
      (2026-09-18, see docs/LOG.md). `capture_failure()` used to only
      write `plays/_failures.jsonl`; `find_best_prior()` only reads
      `plays/_history.jsonl`, which only `capture_success()` wrote to, so
      a product that never once succeeded got zero benefit from any
      analysis round, confirmed via the audit log (`improving_on_prior:
      true` had never fired, ever, on a real product). Fixed:
      `capture_failure()` now also appends to `_history.jsonl` (new
      `outcome` field), propagated through `stg_success_history.sql`/
      `performance_history.sql` (`engagement_outcome` column) too.
- [x] **discuss.py's product/brand identity is now explicitly locked**
      (2026-09-18) — a recommendation like "lean into the Cat in the Hat
      trend" could read as "rename the product." Both the proposal and
      judge prompts now say the product name/brand is fixed, only a
      content theme or wording change is in scope.
- [x] **`--target-region`/`--target-audience` on `contentmaster run`**
      (2026-09-18, see docs/LOG.md) — lets a product declare who it's
      actually trying to reach, persisted per-product
      (`topic_index.save_target`/`load_target`), threaded into both
      draft_generator.py's prompts and discuss.py's proposal prompt
      (which now prefers the real target over the trending context's
      generic top regions when they conflict). Testing phase: region
      only in real use; `audience` exists but isn't exercised yet.
      **Explicitly not built yet** (noted, not scheduled, per the user's
      own scoping call): the Google Trends fetch itself is still a fixed
      "top trend" pull that doesn't filter by the target at all. A real
      "reach this specific target" fetch, and the richer future dimension
      set (audience/age/gender, not just region), plus the DuckDB/dbt
      schema changes a richer trend table (e.g. joined by region as a
      foreign key) would need, are all still open.
- [x] **discuss.py's judge/synthesis pass turned off by default**
      (2026-09-18) — `DISCUSS_JUDGE_MODEL` defaults to `DISCUSS_MODEL_A`,
      so it was really "model A judges its own proposal," not real
      third-party judgment, and this machine has no other local chat
      model pulled (`ollama list` confirmed) and is already
      memory-constrained. Code kept, gated behind `_JUDGE_ENABLED`
      (env `DISCUSS_JUDGE_ENABLED=1` to turn back on once a genuine
      third-party judge model is wired up via `DISCUSS_JUDGE_MODEL`).
      `synthesize_strategy()` currently returns both raw proposals,
      labeled, instead of a merged "final strategy."
- [ ] **Analysis timing is wrong — metrics are read immediately after
      publish, not after they've had time to mean anything.** Flagged
      2026-09-15: `step6_track_metrics` pulls metrics seconds after
      publish (real example: `impressions: 1, ctr: 0.0`), and
      `analyze_performance()`/`synthesize_strategy()` run on that
      same-instant data — so `trend` and "did the prior suggestion work"
      are being computed from noise, not real audience response, and
      `discuss.py`'s proposals end up repetitive because the evidence
      barely differs run to run. Redesign (see docs/LOG.md 2026-09-15 for
      the full /grill-me discussion) — **built and verified for all 3
      tiers, 2026-09-15** (see the correction below — 7d/30d were
      initially thought unwired, that was inaccurate, actually just
      unexercised since no post had reached 7/30 days yet in practice):
      decouple publish from analysis into 3 scheduled checkpoints instead
      of 1 synchronous step — **each checkpoint runs a full
      analyze+discuss+capture cycle (decided Round 2, reversing Round 1's
      "24h is just a light check"), but asks a different, appropriately-
      scoped question rather than judging overall success/failure too
      early**:
        - **24-48h ("immediate reaction")**: did the algorithm actually
          push this out at all (early reach vs. this account's own
          historical 24h-tier baseline — *not* compared against 7d/30d
          baselines, that would be comparing different things)?
        - **7 days ("golden verdict window")**: is the heat sustained —
          compared against other posts' 7-day-tier readings.
        - **30 days (batch review)**: long-term/time-series performance,
          aggregated across all posts published that month — a different
          *shape* of analysis (monthly rollup, not per-post).
      Build order: **24-48h first** (decided Round 1, reasoning: fastest
      way to prove the checkpoint/scheduling mechanism works in a real
      environment without waiting a week), 7-day and 30-day follow once
      that's proven out.

      **What's built (2026-09-15):** new `src/contentmaster/tracking_review.py`
      (queue/find_due/mark_checkpoint_done, `plays/_tracking_review.jsonl`);
      `modiqo_play.capture_success()`/`capture_failure()`/`_append_history()`
      take a `checkpoint` param, new `find_best_prior()` (the 2-tier 7d>24h
      fallback); `analysis.analyze_performance()` takes `checkpoint`, scopes
      all comparisons to that tier via a new `checkpoint` column on
      `performance_history` (and the 2 staging models under it — both now
      use explicit `read_json`/`columns=` schemas, not `read_json_auto`
      inference, so old pre-`checkpoint` rows read null instead of making
      the column vanish — same fix already applied to `ts` earlier);
      `pipeline.py`'s `run()` now stops at publish + queuing (`step6_track_metrics`
      and the old `step7a_analyze_and_discuss`/`step7_modiqo_capture` are
      gone), new `step_checkpoint_analyze()`/`run_review()`; new CLI command
      `contentmaster review [--checkpoint 24h|7d|30d]`. **Correction
      (2026-09-15, same day)**: earlier notes here said only `24h` had
      real due-date logic — that was wrong. `tracking_review.find_due()`
      and `pipeline.step_checkpoint_analyze()` were never 24h-specific,
      they're generic over whatever `checkpoint` string is passed; `7d`
      and `30d` just hadn't been *exercised* yet since no real post had
      reached that age. Verified directly: backdated a test entry 8 days
      and ran `contentmaster review --checkpoint 7d` for real —
      `find_due("7d")` found it, `analyze_performance(checkpoint="7d")`
      correctly showed the 7d-specific "is the heat sustained" question,
      `capture_failure(..., checkpoint="7d")` correctly tagged the
      result. So: 24h, 7d, and 30d are all *functionally* done; what's
      still actually missing is 30d's *different-shaped* batch/aggregate
      analysis (today's `analyze_performance()` only knows how to do a
      single-post-vs-this-tier's-history comparison, not a monthly
      rollup across every post) — see the still-open item below.
      Earlier end-to-end verification (queue -> due -> pull -> analyze
      (checkpoint-labeled evidence) -> discuss (real Ollama calls) ->
      capture -> `checkpoints_done` correctly prevents reprocessing) used
      a manually-backdated test entry with mock metrics, no live platform
      call. `dbt run`/`dbt test` both clean except the 2
      pre-existing, already-documented, unrelated issues (`stg_plays`'s
      empty-glob error, the 3 old failure rows missing `ts`).

      Design session via /grill-me, started 2026-09-15 — Round 1 + Round 2
      decided (full Q&A in docs/LOG.md):
      - Publish stops pulling metrics entirely — the immediate
        `step6_track_metrics` call is removed from `run()`, full stop.
        Writing an entry to `plays/_tracking_review.jsonl` (below) *is*
        the "confirmed it really published" record.
      - **`plays/_tracking_review.jsonl`** (new file, append-only, same
        pattern as `_history.jsonl`/`_failures.jsonl`). Fields:
        `post_id, product, channel, post_ref, final_text, ts` (when
        published), `edited` (bool — reusing `human_loop.ReviewDecision`'s
        existing field, not inventing a new name), `reviewer_note` (also
        reused as-is from `ReviewDecision` — already holds "why edited";
        rejection reasons don't belong here at all, they already have a
        home in `plays/_failures.jsonl`'s `reason` field via
        `capture_failure()`, since a rejected draft never gets published
        and so never reaches this file), `checkpoints_done` — **a dict of
        checkpoint name → the timestamp that checkpoint actually ran**
        (not a bare list), e.g. `{"24h": "2026-09-16T09:00:00Z"}`, so a
        checkpoint run late (the job wasn't triggered exactly on time)
        can be corrected for instead of silently assumed on-schedule.
        "Due" check has no upper window — anything past its threshold and
        not yet in `checkpoints_done` is due, however late.
      - `capture_success()` (writes `plays/*.json` snapshot +
        `plays/_history.jsonl` ledger) fires from **every** checkpoint,
        not just one. Consequence: `plays/_history.jsonl` needs a
        `checkpoint` field per entry (multiple rows per `post_id` now,
        one per checkpoint tier) so tier-specific comparisons don't mix
        with each other; `performance_history`'s dbt mart needs the same
        new column threaded through. `plays/*.json`'s existing
        "overwritten by the latest call" behavior is kept as-is (it's a
        "current state" snapshot, not history — the checkpoint-level
        history lives in `_history.jsonl`, which already exists for
        exactly this job).
      - `analyze_performance()` needs a new `checkpoint` parameter so its
        query/evidence-building stays within one tier (comparing this
        post's 24h reading against other posts' 24h readings only, never
        against 7d/30d baselines).
      - Trigger mechanism — **decided**: a new CLI command,
        `contentmaster review`, run manually (or the user calls it
        themselves from their own launchd/cron entry). This part is
        settled, not TBD. **What *is* labeled TBD**: an *automatic* mode
        — `contentmaster review` (or a flag on it) deciding on its own,
        based on each entry's `ts`, when to run itself in-process, rather
        than relying on the user (or an external cron) to invoke it.
        Not designed yet, to be discussed before/while building.
      - `_print_run_summary()`'s end-of-run text changes (metrics are no
        longer pulled synchronously) — stays in English, matching the
        rest of the project's existing CLI/print text.
      - `draft_generator`'s `prior` lookup fallback — **decided Round 3,
        corrected**: this fallback is a **2-tier choice between 24h and
        7d only** — prefer 7d over 24h if both are available, else
        whichever exists. **30d is not part of this per-post ladder at
        all** — it's a different *kind* of analysis (aggregate/
        comparison across all of that month's posts, not a single-post
        reading), so "prefer 30d over 7d" isn't a meaningful comparison
        the way "prefer 7d over 24h" is. How the 30d rollup actually
        feeds back into `draft_generator`/`discuss.py` (if at all) is
        still open — not designed yet. If none of 24h/7d exists anywhere
        (true cold start), write ungrounded — same as today's
        `no_history` path.
      - **Future idea, not part of this build** (2026-09-15): once enough
        posts have *both* a 24h and a 7d reading, it may be worth going
        beyond "prefer the more mature tier" and instead modeling how the
        two relate — e.g. which 24h patterns (hook style, early
        engagement shape) best predict a post that both explodes at 24h
        *and* keeps sustaining through 7d, and feeding that weighting
        into `discuss.py`'s prompt or a future fine-tuning pass (ties to
        Phase 8). Needs real volume in both tiers first — not
        actionable yet, just recorded so it isn't lost.
      - (A version of the fallback that also warns the human on the
        review screen was considered — see Notes section at the end of
        this file, not accepted for now.)
- [ ] **`trending_context`/`external_signal` repeat verbatim across
      analysis rounds** because the underlying Google Trends seed data
      is static (not refreshed — see the automation item above) and the
      query is a fixed top-N, so nothing about the output actually
      changes between rounds. Two options raised 2026-09-15: (a) change
      the query to detect/surface novelty, or (b) only query when new
      external CSV data has actually landed. Leaning toward a hybrid:
      keep querying (cheap), but compare against what was stored in the
      *last* captured analysis (`plays/_history.jsonl` already has
      `trending_context` per entry) and omit/relabel it as "unchanged
      since last round" when identical, rather than re-presenting stale
      data as if it were fresh signal each time. Not implemented.
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
- [x] dbt tests on `performance_history` — done 2026-09-15, see Phase 1's
      dbt-tests item (not_null on `ts`/`product`/`channel`,
      accepted_values on `status`).
- [x] **Analysis confirmation moved from "confirm the raw numbers" to
      "confirm metrics + recommendation together" — done 2026-09-16.**
      The old flow confirmed `analyze_performance()`'s verdict *before*
      `discuss.py` had even run — blessing numbers with no recommendation
      to react to yet. User raised this as an open question (not sure
      which was better); resolved with a 3rd design (not just "confirm
      analysis" or "regenerate on disagree"): **log both** — the human's
      feedback is stored *alongside* the LLM's recommendation, not in
      place of it, and next time a draft is written for this
      product/channel, both get read back.
      `analysis.analyze_performance()` no longer confirms internally
      (dropped its `interactive` param); new `analysis.confirm_with_human
      (result, recommendation)` is called from
      `pipeline.step_checkpoint_analyze()` *after* `synthesize_strategy()`
      — one combined screen, one `[Y]es/[n]o` prompt.
      `modiqo_play.find_best_prior()` gained a `human_feedback` field
      (pulled from the stored `analysis.human_note`); `draft_generator.py`'s
      prompt (both the topic-based and the older context-based path)
      includes it alongside `improvement_note` when present, explicitly
      told to prioritize the human's direction where the two conflict.
      Verified: `confirm_with_human()`'s combined screen + disagree path
      tested directly; full round-trip tested end to end —
      `capture_success()` with a human note -> `find_best_prior()` ->
      `draft_generator.draft_posts()`'s actual prompt text confirmed to
      contain both the LLM's suggestion and the human's feedback, in that
      order, with the priority instruction.
- [x] **Cognee extraction was one fixed query ("product name, key
      features, target ICP") re-run every single time, handing every
      draft the same blob of text — real bug found via production
      testing 2026-09-15**: `--product-name` doesn't change what gets
      extracted from the *same* whitepaper, and a brand-new product name
      has no `prior` yet either, so every cold-start run hit an
      identical prompt and a small local model converged on near-
      identical phrasing ("this same content have been written" — the
      user's own words rejecting 3 drafts in a row). **Fixed**: new
      `src/contentmaster/topic_index.py` — Cognee now returns a *list*
      of topics (brief one-liners) instead of one fixed Q&A answer, only
      re-extracted when the whitepaper file's content hash actually
      changes (zero Cognee calls otherwise — real compute savings, not
      just fewer tokens), reviewed once by a human (batch accept, or
      per-item edit/delete/type-in-your-own), then `draft_generator.py`
      writes one post per *least-used* topic so variety comes from real,
      distinct extracted facts rather than a hardcoded per-draft prompt
      template. Usage counts only bump on a successful publish, not at
      draft-write time. Full design + verification in docs/LOG.md
      2026-09-15 (a second /grill-me session). `draft_generator.py`'s
      temperature also raised 0.7 -> 0.8 in the same pass.
- [x] **Two real UX bugs found via an actual production run of the topic
      index feature above, both fixed 2026-09-15** (full detail in
      docs/LOG.md): (1) topic review's `t` option was silently swallowed
      into "accept all" — input is now locked to exactly a/c/t, no blank
      default, explicit error otherwise. (2) draft review's `[e]dit`
      took typed feedback as the literal post text — **a real broken
      post went live because of this** (not reverted, the user's own
      account/call). Fixed with a real redesign, not a wording tweak:
      new `DraftGenerator.revise_post()` + a `[f]eedback` option in
      `human_loop.review_draft()` that sends feedback to the LLM and
      shows the revision before it can be approved, plus a confirmation
      step on `[e]dit` itself.
- [x] **Full read-only code scan (2026-09-17), then fixes via `/grill-me`
      with a red/yellow/green risk framework** (green = fix directly, no
      need to ask; red/yellow = grill first) — full detail in
      docs/LOG.md. Findings and outcomes:
      - 🔴 **Real, active bug**: `warehouse/models/staging/stg_plays.sql`'s
        `plays/*.json` glob collided with the new topic-index files
        (`plays/{product}_topics.json`, different schema) — `dbt run`
        genuinely errored. **Fixed**: topic index files moved to their
        own `plays/topics/{product}.json` subdirectory (new
        `topic_index.TOPICS_DIR`), `.gitignore` updated to match.
        Confirmed `contentmaster review`/`step_checkpoint_analyze()` has
        zero coupling to this path (grepped — every `topic_index`
        reference in `pipeline.py` lives inside `run()`'s flow, not
        `run_review()`'s). Verified: `dbt run` no longer errors on the
        collision (back to the older, already-known "empty glob, unused
        by any Python code" state); the real existing topic index
        (`Fluffy roomate`, 4 topics) migrated and confirmed loading
        correctly from the new path.
      - 🟡 **3 duplicate slugify functions, one unsafe.**
        `topic_index._slug()`/`pipeline._dataset_slug()` were identical;
        `modiqo_play._play_key()` was weaker (only lowercased + replaced
        spaces, didn't strip other special characters — confirmed via a
        real product name used this session, "Hairy, furry, cute little
        monster", which has commas). Checked whether merging was safe
        before doing it: zero `plays/*.json` snapshot files existed at
        the time (nothing to orphan). **Fixed**: new
        `src/contentmaster/slug.py`, one `slugify()` function, all 3
        call sites now use it — `_play_key()` now sanitizes both
        `product_name` and `channel`, not just replacing spaces.
      - 🟢 (fixed directly, no grilling needed): `topic_index.py`'s
        `[e]dit or [d]elete` sub-prompt silently ignored invalid input
        (inconsistent with every other prompt in the file, which all
        error) — now errors and re-prompts. `stg_plays.sql`'s comment
        still referenced the pre-rename `src/automarketer/` path — fixed
        to `contentmaster`. `metrics_store.mock_metrics()`'s docstring
        didn't mention the checkpoint-time real-API-failure fallback
        case — added.
      - Reconfirmed still true, not new: `campaign_performance`/
        `stg_plays` genuinely have zero Python consumers (matches the
        2026-09-13 audit). All previously-flagged pending-delete files
        (`rocketride_client.py`, `improve.py`, the 2 rule files,
        `.rocketride/`, 2 test `.json` snapshots) are confirmed actually
        gone now — no longer pending.

## Phase 10 — success judgement redesign (gates + percentile + eras)

**Built 2026-09-20/22** — see the checklist below for what is done and what
is not; `src/contentmaster/scoring.py` holds the score, the gates and every
tunable constant. Designed 2026-09-19. Replaces the whole
"is this post a win?" mechanism. Trigger: the absolute threshold
`SUCCESS_CTR_THRESHOLD = 0.5` (`pipeline.py:76`) was passing 5 of 12 real
posts (42%) on an account that has never once been reposted, replied to,
or had more than one real like — i.e. the threshold was not measuring
anything.

### Why the current mechanism cannot work

- **The score cannot tell 1 like from 2 likes.** `pipeline.py:352`
  computes `ctr = weighted / max(total, 1)` — a weighted *average*, not a
  sum. A likes-only post scores exactly 1.0 however many likes it has; a
  zero-engagement post scores 0. The real distribution over 12 posts is
  therefore `[1,1,1,1,1,0,0,0,0,0,0,0]`, and 5/12 clear 0.5. Any
  percentile rule laid on top of *this* score degenerates: p75 = 1.0 =
  the maximum, so "strictly greater than p75" can never select anything.
  **Switching to a percentile and switching to a volume-sensitive score
  are one decision, not two.**
- **Median would not have fixed it either.** median = 0, "> 0" passes the
  same 5 of 12 (42%). p75 = 1, strictly-greater passes 1 of 12 (8%) —
  percentile rules make a win rare by construction at any account size,
  so the threshold never needs re-tuning. That is the reason to use one;
  the exact number 75 is not the interesting part.
- **One of those likes is a Bluesky auto-bot.** Real ceiling across all
  12 posts is 1 like; real total engagement is 5 events.
  `platforms/bluesky.py:161-167` reads `like_count` straight through, so
  bot/self engagement counts as real — and the pollution is one-way and
  cumulative.
- **The baseline never accumulates.** Plays and history are keyed on
  `(product, channel)`, but the product name is hand-typed and changes
  every run — `plays/_history.jsonl` holds 4 products across 6 rows
  ("Your best stress reliever" ×3, "Fluffy roomate" ×1, plus 2 test
  rows), so n is always 1 and there is never anything to compare against.
- **Failure overwrites success.** `modiqo_play.py:107` overwrites
  `winning_text` on every capture, so a below-baseline post destroys the
  best-known one.

### The new rule

Score, computed for *every* post at every checkpoint — a zero-engagement
post still belongs in the baseline, since computing a percentile from
winners only is survivor bias:

```
score = 3×reposts + 3×bookmarks + 1×likes + 1×replies   (bot-filtered)
```

The high-signal tier (weight 3) is repost and bookmark: both cost the
reader something real — a public endorsement, or a private "I want this
later". Likes are near-free at this account size and replies can be spam
or hostile, so both sit at weight 1. **Reply value is deliberately not
carried by the score** — it comes from reply-content analysis instead, so
the two do not have to compromise on one number. Quote posts are captured
but not scored (a quote can be a dunk); they go down the same
content-analysis path as replies.

Gate 1 — is there a usable baseline? Same channel, same checkpoint tier
(24h only ever compares against 24h), same account era:

```
posts >= 8  AND  total engagement events >= 10
```

The second condition exists so "8 posts that are all zero" cannot be
mistaken for a baseline. Fails → `hypothesis`; no win/lose judgement is
made at all.

Gate 2 — did this post clearly stand out? Both conditions required:

```
quality:  reposts + bookmarks >= 1
volume:   score > p75(baseline scores)
```

The quality condition is what makes the rule discriminating, and it also
covers the degenerate case where p75 collapses to 0 (>75% of posts with
no engagement at all), which would otherwise bring "1 like wins" back. An
absolute score floor was considered and dropped as redundant with it.

Resulting states, replacing today's success/failure binary:

| state | when | effect |
|---|---|---|
| `hypothesis` | gate 1 fails | writes a falsifiable hypothesis as the next round's `improvement_note` |
| `hypothesis_rejected` | falsification criterion met | records the direction as dead; never proposed again |
| `proven` / `proven_tentative` | both gates pass | stored as `winning_text`; `_tentative` while 8 <= n < 20 |
| `likes_only` | quality condition fails | not a win |
| `below_baseline` | volume condition fails | **does not overwrite `winning_text`**; accumulates evidence of what stays under the line |

Confidence is carried by the tier label, not by the threshold — so having
8/10 wrong costs a conservative label rather than a false positive.

`proven` is evidence, never an instruction. The prompt wording is "this is
the best-performing post so far and the reasoning for why it may have
worked — extend it, improve it, or disagree with reasons", not "write like
this". Hypothesis mode does not switch off once a `proven` exists: every
round still produces something falsifiable, and a `proven` followed by N
below-baseline posts in the same direction is automatically demoted back
to `hypothesis`. A single fixed winning pattern will eventually stop
working, and the system has to be able to notice that about itself.

### Account eras

All of the above is being designed from an **extreme** starting point that
the data alone does not reveal: the test account
(`content-master-ai.bsky.social`) follows nobody, has no avatar, no bio,
no interactions of any kind, and a handle that reads as a bot to both
Bluesky's ranking and to humans. The 0-engagement posts are therefore not
evidence about content quality — reach is ~0, and content quality is not
measurable through it. Three consequences:

- **The first hypothesis must be about reach, not copy.** Testing
  statements-vs-questions while reach is ~0 burns 8 posts on a result
  that cannot be interpreted either way (was the copy wrong, or did
  nobody see it?). Profile completion + following accounts in the same
  space + hashtags comes first.
- **`account_era` becomes a required field on every metrics row**, written
  by the system at publish time, defaulting to 0, incremented only by an
  explicit declaration (never back-filled — back-filling will miss rows).
  era 0 is *not* "those 12 posts": it is the entire period until the
  checklist below is installed and tested, however many posts that turns
  out to be. Baselines only ever compare within one era. Without this,
  era 0's near-all-zero rows would hold p75 at the floor exactly when the
  account starts getting real reach, and the system would start declaring
  false `proven`s at the worst possible moment.
- era 0 data is isolated but **never deleted** — it is the control group
  for the reach hypothesis. "No profile, no following" performance already
  exists as a measured baseline, so era 1's first posts can be compared
  against it directly instead of spending 8 posts re-measuring it.

Every metrics row also stores an account-state snapshot at publish time
(followers, following, has avatar, has bio), and raw counts are always
stored rather than only the derived score, with the constants living in
one place. None of 8 / 10 / p75 / 3:1 can be honestly calibrated from
inside a vacuum; the point is to be able to re-run history against new
constants later instead of having to re-collect it.

### era 1 entry checklist

**Blocking** — era 1 cannot start until these are done, because each one
would otherwise corrupt era 1's data at the source:

- [ ] Filter bot and self engagement out of all counts (judgement
      criteria still to be decided)
- [x] Read `bookmark_count` and `quote_count` in
      `platforms/bluesky.py`'s `get_post_metrics()`. **Already available
      in the installed SDK** (atproto 0.0.72 — `PostView.bookmark_count`
      at `models/app/bsky/feed/defs.py:29`, `quote_count` at `:45`, both
      `Optional[int]`), so no new endpoint is needed; the existing
      `get_posts()` call already returns them. Still unverified: whether
      they come back as real integers or `None` for this account.
- [x] Score becomes the weighted sum above, replacing `pipeline.py:352`'s
      weighted average
- [x] Gate 1 / gate 2 and the state set above
- [x] `below_baseline` stops overwriting `winning_text`
      (`modiqo_play.py:107`)
- [ ] `account_era` + account-state snapshot on every metrics row
- [x] Raw counts always stored; constants centralised and re-runnable
- [ ] Product identity moves to the whitepaper's content hash.
      `topic_index.py` already computes and stores one (`source_hash`,
      `topic_index.py:226`, sha256 of the file's bytes at `:48`) — it is
      only the *filename* that is still keyed on the hand-typed product
      name (`topic_index.py:45`). Making the hash the identity means
      renaming a product cannot fragment its baseline, with no alias table
      and no separate topic-domain concept needed (the whitepaper *is* the
      domain boundary). Includes unifying the existing rows and having the
      CLI/Streamlit remember the last used settings.
- [ ] **Manual, not code**: complete the account profile — avatar, bio, a
      name that does not announce itself as a bot, and following accounts
      in the same space. This is the independent variable of the reach
      hypothesis; starting era 1 without it contaminates the first test.

**Non-blocking** — can land after era 1 starts without making the data
wrong:

- [ ] Fetch reply text (`getPostThread`) and classify it with the local
      LLM, reasons required, never a bare label: positive scores;
      negative-and-specific goes straight into `improvement_note` at
      higher priority than any statistic; negative-and-purely-hostile is
      recorded but moves nothing; neutral/question marks "someone was
      willing to engage". Credibility signals: the replier's follower
      count and posting history, and whether the criticism is specific.
      Most important — whether it is *verifiable*: a reply pointing at a
      factual error the whitepaper itself can settle needs no credibility
      judgement at all, just verification.
- [ ] Hypothesis mode proper: hypothesis generation with stated reasoning,
      a falsification criterion fixed in advance (and not editable after
      the fact), and rejected directions recorded so the same dead end is
      never proposed twice.
- [ ] `proven` prompt wording as evidence-not-instruction, plus the
      demotion rule (N still to be decided)

### Implementation trap: the era filter has more than one read site

Filtering by era at the gate alone is not enough. History is read in at
least four places — `modiqo_play.find_best_prior()` (straight off
`plays/_history.jsonl`), `analysis.analyze_performance()` (via the
`performance_history` mart), `warehouse/models/staging/stg_success_history.sql`,
and `warehouse/models/marts/performance_history.sql`. Filtering in Python
but not in dbt leaves era 0 rows visible in the marts, and that leak is
silent — no error, just a quietly wrong baseline. Carry `era` as a
required column from staging upward so downstream models cannot ignore it.
`stg_success_history.sql` has already been bitten twice by the
missing-column-in-old-rows problem (`ts`, then `checkpoint`); `era` is the
third, so use an explicit `read_json` schema from the start rather than
waiting for it to bite again.

### Built since the design session (2026-09-20/22)

- `scoring.py` — the weighted sum, gate 1, gate 2, the five states, and
  `control_arm_size()`. Every constant lives here so stored history can be
  re-run against new ones. `SUCCESS_CTR_THRESHOLD` is deleted.
- `bookmark_count`/`quote_count` **verified live** on this account: Workers
  AI's `getPosts` returns real integers, not `None`. Quote posts are
  captured and deliberately never scored.
- The `winning_text` overwrite is fixed structurally, not by a guard —
  only a win reaches `capture_success()`, so `below_baseline` cannot
  reach it at all.
- Field names follow the arithmetic: `ctr` -> `engagement_score`, and
  `impressions`/`clicks`/`conversions` are gone (they were ad-tech names
  holding social numbers, and stored the same figure twice once raw counts
  landed).
- `metrics_store.mock_metrics()` is **deleted**. Nothing invents a number
  any more: a failed pull writes nothing and leaves the checkpoint due, and
  the tracking ledger refuses a post with no platform reference.

Still open from the blocking list: bot/self filtering (needs the actor
list, not just the counts), `account_era`, and product identity moving to
the whitepaper hash. The manual profile work is unchanged.

### Still to decide (Phase 10)

- [ ] Bot/self engagement judgement criteria
- [ ] How reach is measured at all (Bluesky exposes no impressions) — this
      blocks writing the reach hypothesis's falsification criterion
- [ ] The hypothesis record's data structure
- [ ] Reply-classification implementation criteria
- [ ] N for the `proven` demotion rule
- [ ] How an era bump is declared (CLI command? flag? file?)
- [ ] Whether `quote_count` ever scores (currently: captured, not scored)
- [ ] Final confirmation of gate 1's 8 / 10 — deliberately left provisional
      until era 1 produces real data to calibrate against

## Phase 11 — scheduled, not built

Decided 2026-09-22 (/grill-me), deliberately left for later.

- [ ] **Generate-image toggle in the review UI.** On: the pipeline
      generates an image with FLUX. Off: it uses whatever the user put in
      `product_assets/<product>/` and never calls the image model. Today
      the rule is automatic — supplied files win when they exist,
      generation happens when the folder is empty (product_assets.py) —
      which covers the common case but gives no way to say "I have assets
      but I want a generated one this time", or the reverse.
- [ ] **Full error index: retrieval, not just counting.** `error_index.py`
      currently answers "which errors keep happening" so they can be
      prevented. The scheduled half is feeding a past error's recorded
      analysis back to the LLM as context so it can propose a fix, which
      then goes through normal human review. **Required for era 1.**
- [ ] **Platform-level learned rules.** Findings verified about a platform
      rather than a product ("posts with links reach fewer people here")
      currently hang on the product, so every product rediscovers them.
      Deliberate: a product-level fact misfiled as a platform fact would
      poison every other product, and with one product the duplication
      costs nothing yet.
- [ ] **Error index for humans.** The counter is built for the pipeline;
      a view aimed at whoever is debugging is a separate shape.
- [ ] **Automatic Google Trends refresh.** The seeds are hand-exported CSV
      (`warehouse/seeds/`), currently ending 2026-09-13. Generation states
      the capture date in the prompt so the model can judge staleness for
      itself, which is honest but not a substitute for fresh data.

## Notes — considered, not accepted (revisit only with new info)

Ideas that came up and were deliberately *not* adopted — not because
they're wrong, but because there wasn't enough reason to build them yet.
Not a backlog item (no `[ ]`) — don't pick these up just because they're
listed; only bring one back to an actual Phase if something changes that
makes it worth reconsidering (more data, a real complaint, a new
constraint), and note what changed when you do.

- **Reference note (not a design idea — how the engagement score is
  actually computed, recorded 2026-09-17, corrected 2026-09-18 after a
  real analysis session found the original formula backwards).**
  Bluesky's API exposes no impressions count at all, so
  `pipeline._pull_checkpoint_metrics()` approximates it from engagement.
  The original formula (`clicks / max(likes+reposts+replies, 1)`)
  actively punished a post for getting *more* positive engagement, since
  a repost or reply grew the denominator without growing the numerator.
  Replaced with a weighted average instead, bounded to the same interval
  regardless of total engagement:
  ```
  total    = like_count + repost_count + reply_count
  weighted = ENGAGEMENT_WEIGHTS['like']*like_count
           + ENGAGEMENT_WEIGHTS['repost']*repost_count
           + ENGAGEMENT_WEIGHTS['reply']*reply_count
  ctr      = weighted / max(total, 1)   # still called "ctr" everywhere on purpose, see Phase 9 below
  ```
  Weights (`pipeline.ENGAGEMENT_WEIGHTS`): like=1, repost=3, reply=2.
  `SUCCESS_CTR_THRESHOLD` moved 0.02 -> 0.5 to match the new scale (0 = no
  engagement at all; any real engagement clears 0.5 with these weights).
- **Warn the human on the draft-review screen when writing ungrounded
  (no post anywhere has a completed 7-day analysis yet).** Considered
  2026-09-15 alongside the `draft_generator` cold-start fallback (Phase 9).
  Not accepted for now — `step4_human_review` already shows the draft
  itself for approval/edit/reject, and a separate "no data yet" notice
  would be a second, overlapping human touchpoint for the same decision
  rather than new information. Revisit if the cold-start period turns out
  to last long enough in practice that reviewers are repeatedly caught
  off guard by ungrounded drafts.

- **Cross-platform comparison of the same content.** Raised 2026-09-15
  while confirming `plays/*.json` is keyed by `(product, channel)` — the
  same product posted to 2 different platforms (e.g. Bluesky and X) gets
  2 fully independent play files/histories today, even if the posted
  text is identical. There's currently no mechanism to compare "did this
  same content do better on platform A vs. platform B." Not accepted for
  now — no request to build it, just noted while explaining existing
  behavior. Revisit if/when the same content is deliberately cross-posted
  often enough that this comparison would actually inform strategy.
