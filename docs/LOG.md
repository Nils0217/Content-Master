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

## 2026-09-13 — Packaged as a real CLI

- Added `pyproject.toml` ([project.scripts]) + `src/automarketer/cli.py`
  so `pip install -e .` registers `automarketer` as an actual command,
  with `run` and `bluesky verify` subcommands, instead of remembering
  `./.venv/bin/python run_pipeline.py ...` / `scripts/verify_bluesky.py`
  paths. Both old entry points kept as thin deprecated wrappers that
  forward into the same CLI code, so nothing that already worked broke.
- `requirements.txt` cleaned back down to the package's actual direct
  dependencies (mirrors `pyproject.toml`) — `pip freeze` had accumulated
  the entire `dbt-duckdb` transitive tree from the warehouse work, plus a
  self-referential `-e git+https://github.com/...#egg=automarketer` line
  from freezing an editable install pointed at this repo's own GitHub
  remote. Neither belonged in the package's own dependency list — see
  `docs/ERROR_LOG.md`.

## 2026-09-13 — Platform adapter interface; renamed automarketer → contentmaster

- Corrected a design mistake from the CLI-packaging work: Bluesky had been
  wired in as a Bluesky-specific top-level CLI subcommand
  (`automarketer bluesky verify`). Adding IG/FB/X/YouTube/Mastodon the same
  way would mean a new special-cased subcommand tree per platform. Replaced
  with a generic `Platform` adapter interface
  (`src/contentmaster/platforms/base.py`: `test_connection`,
  `fetch_public_posts`, `publish_post`, `get_post_metrics`, plus generic
  `PlatformConfigError`/`PlatformAuthError`/`PlatformRateLimitError`/
  `PlatformAPIError` exceptions) and a `registry.py` (`PLATFORMS` /
  `PLANNED_PLATFORMS`) that `pipeline.py` and the CLI look platforms up in
  by name. `bluesky_client.py` became `platforms/bluesky.py`, the first
  (and so far only) real implementation; Mastodon/X/Instagram/Facebook/
  YouTube are named placeholders in `PLANNED_PLATFORMS`, not stub classes.
  CLI: `automarketer bluesky verify` → `contentmaster connect <platform>`
  (works for any registered platform); new `contentmaster platforms` lists
  what's implemented vs. planned. `pipeline.py`'s `step5_publish` /
  `step6_hotdata_metrics` now branch on "is this channel in PLATFORMS",
  not "is this channel == bluesky".
- Renamed the project `automarketer` → `contentmaster`: package directory
  (`src/automarketer/` → `src/contentmaster/`), CLI command
  (`automarketer` → `contentmaster`), `pyproject.toml` project name, dbt
  project/profile name (`automarketer_warehouse` → `contentmaster_warehouse`
  in `warehouse/dbt_project.yml` + `warehouse/profiles.yml` — re-ran `dbt
  run` after to confirm the rename didn't break anything), Cognee's default
  dataset name, Rote's workspace name. Deliberately did NOT rename
  `hotdata_client.py`'s `CATALOG = "automarketer"` — that's the name of an
  already-existing external hotdata.dev catalog, not the project's own
  name; renaming it would just point at a different, empty catalog (see
  `docs/ERROR_LOG.md`). `pipelines/automarketer-content-gen.pipe` also
  left un-renamed — it's a real file with that literal name.
- `src/automarketer/` (the pre-rename package) could not be deleted —
  `rm` is blocked in this dev environment — so it was excluded from
  packaging instead (`pyproject.toml`'s `packages.find.include`) and left
  as inert dead code nothing imports. Flagged in `README.md` and
  `docs/SCHEDULE.md` for manual deletion.
- Verified: `contentmaster --help` / `platforms` / `connect bluesky` (auth
  succeeded against the real account) all work post-rename; the old
  `scripts/verify_bluesky.py` wrapper (now forwarding to `connect bluesky`)
  still works too; `dbt run` still builds all 4 models after the
  warehouse rename.

## 2026-09-13 — Real analysis step: track -> analysis -> discuss -> log

- User flagged a real gap: the old loop went straight from one post's raw
  numbers to a single local model's "improve" suggestion — no comparison
  against history, no check on whether the *previous* suggestion actually
  correlated with anything moving. Agreed this wasn't analysis, it was one
  model guessing from N=1; open-ended multi-turn model debate was pushed
  back on (same-model self-debate mostly just agrees with itself, and cost
  scales badly) in favor of one bounded round: 2 different local models
  propose, 1 synthesizes.
- Root cause of "no history to analyze against": `plays/*.json` is a
  snapshot overwritten every run — at most one data point per
  product/channel, ever. Fixed first, since analysis is impossible without
  it: `modiqo_play.py` now also appends every successful capture to
  `plays/_history.jsonl` (append-only), backfilled from the two existing
  play snapshots. New dbt models `stg_success_history.sql` +
  `performance_history.sql` (union of published-over-time + rejections)
  give analysis.py a real timeline to query — not just campaign_
  performance's current-state snapshot.
- `src/contentmaster/analysis.py` (new): queries
  warehouse/local.duckdb's `performance_history` for this product/channel,
  computes n_prior_posts / historical_avg_ctr / trend (labeled
  "insufficient_for_trend" below n=2 — no fabricated confidence from tiny
  samples) / whether the *previous* improvement_note's suggestion looks
  like it correlated with ctr moving, then — human-in-the-loop, as
  requested — prints the verdict and lets a human confirm or override it
  (`Y/n`, same input() pattern as human_loop.py, reusing
  `ReviewInterrupted` for clean Ctrl-C/EOF handling) before discuss.py
  trusts it.
- `src/contentmaster/discuss.py` (new): 2 real, different local models
  (llama3.2:3b + mistral:latest — both already pulled, no new download)
  each independently propose a next-round strategy grounded in the
  analysis; a 3rd pass (llama3.2:3b, cheapest) synthesizes them into one
  instruction, naming where they agreed/disagreed. Every call logged
  individually via audit.log_event. One bounded round, not an unbounded
  chat — deliberate, see the module docstring.
- `pipeline.py`: `step7_modiqo_capture` now calls
  `step7a_analyze_and_discuss` (analysis + discuss) instead of the old
  single-model `step7b_llm_improve`; the resulting strategy is stored as
  `improvement_note` (so `rocketride_client.py`'s existing
  `prior.get("improvement_note")` read picks it up with no changes needed
  there) plus a new `analysis` dict alongside it on both the play snapshot
  and the history ledger. The per-draft `try/except ReviewInterrupted` in
  `run()` was widened to cover step7 too, since analysis's human
  confirmation is a second interrupt point, not just step4's draft review.
- Also fixed, found while wiring this up: `improve.py` (the now-superseded
  single-model step, kept importable) built its prompt from
  `metrics.get('like_count'/'repost_count'/'reply_count')`, but the metrics
  dict it's actually called with uses `impressions/clicks/conversions/ctr`
  — every suggestion it ever produced was silently based on zeros, not the
  real numbers. See `docs/ERROR_LOG.md`.
- Verified live, full chain: `analyze_performance()` against the real
  warehouse (n_prior_posts=1, trend=insufficient_for_trend, correctly
  identified the prior suggestion as "looks_effective") → human confirmed
  → `synthesize_strategy()` produced two genuinely different model
  proposals + a synthesis → `capture_success()` persisted the analysis
  dict on both the snapshot and history files → re-ran `dbt run` →
  `analyze_performance()` again now sees n_prior_posts=2 and a real
  computed `trend: improving` — the loop closes and compounds, not just a
  diagram.

  Test data note: this test run wrote a real (test) entry into
  `plays/your-best-stress-reliever::bluesky.json` (runs 1→2) and
  `plays/_history.jsonl` — left in place rather than reverted, consistent
  with the existing seeded test data already in `plays/`.

## 2026-09-13 (later) — Removed HydraDB from pipeline.py; two stray Claude Code plugins uninstalled

- User noticed `pipeline.py` still importing `rocketride_client`,
  `hydradb_client`, `modiqo_play` despite the whitepaper saying HydraDB/
  RocketRide aren't used going forward, and asked why. Investigated and
  found `step2_hydradb_persist()` was the actual, concrete reason
  `contentmaster run` had been crashing every time since the local HydraDB
  Docker container was deleted in an earlier cleanup pass — a real bug,
  not just stale naming. Removed it (call + function definition + import)
  and the redundant `HAS_PLAY` graph write in `step7_modiqo_capture`
  (`capture_success()` was already the real source of truth via
  `plays/*.json` + `plays/_history.jsonl`, so nothing was lost). Also
  dropped the now-unused `icp` parameter from `run()`. Verified live: a
  real `contentmaster run` against `Demo-white paper/cat.rtf` now gets
  past Cognee straight into draft generation with no HydraDB event/crash
  at all — confirms the fix, not just a clean compile.
  `hydradb_client.py` itself left in place, unimported (same dead-code
  treatment as `hotdata_client.py`/`src/automarketer/` — `rm` blocked
  here, manual delete command given to the user).
- rocketride_client.py's real role clarified but not yet renamed
  (tracked in `docs/SCHEDULE.md` Phase 0): `RocketRide.draft_posts()`
  calls the local Ollama LLM directly; the actual RocketRide SDK
  (`run_pipe()`/`account_info()`) isn't called anywhere in the real
  `run()` path. The class/file name is legacy from the hackathon and is
  misleading, but renaming touches enough surface (audit event names,
  `step3_rocketride_or_replay`'s name) that it's deferred as its own task
  rather than bundled into this fix.
- User asked how to grant `rm` permission. Root-caused it: not a Claude
  Code setting at all — Rote's shell integration
  (`~/.rote/shell/common/agent_guard.sh`, sourced via `~/.zshrc` ->
  `~/.rote/shell/init.sh`) detects agent-session env vars (`CLAUDECODE`,
  `CLAUDE_SESSION_ID`, etc.) and unconditionally shadows `rm`/`curl`/
  `wget`/`ssh`/`nc`/`node`/`ruby` with functions that just print "Security:
  ... blocked in workspace" and return 1 — no per-command opt-out exists
  in the script. Gave the user the one-line fix (remove the `source
  ~/.rote/shell/init.sh` line from `~/.zshrc`) rather than doing it myself
  — editing a global, always-loaded shell rc file is a persistent
  environment change, not a one-off in-repo edit. Recorded in
  `docs/ERROR_LOG.md` for the next time this class of "why is X blocked"
  question comes up.
- Uninstalled two stray Claude Code plugins the user asked about, both
  user-scope, both unrelated to this repo's own Python code of the same
  name:
  - `cognee-memory@cognee` (source: `topoteretes/cognee-integrations`) —
    this was the thing generating "Cognee memory: recall skipped (auth
    failed)" noise on nearly every turn all session; distinct from the
    project's own self-hosted Cognee Docker container, which stays.
  - `play@play-skills` (source: `modiqo/play`) — Modiqo's own Claude Code
    plugin; distinct from this project's `src/contentmaster/modiqo_play.py`
    (the memory/muscle-memory module `analysis.py`/`discuss.py` depend on
    — explicitly NOT removed, still core).
  Both via `claude plugin uninstall <name>`; confirmed via `claude plugin
  list` (now empty). A new session is needed for the SessionStart hook
  noise to fully stop, since the current process already loaded them.

## 2026-09-13 (later still) — renamed rocketride_client.py -> draft_generator.py

- User pushed back on the earlier "defer the rename" call: didn't
  understand why `pipeline.py` still needed `rocketride_client.py`'s
  `draft_posts()` if the decision was to hit Ollama directly. Answer was
  that they're the same thing (draft_posts() only ever called Ollama; only
  the unused `run_pipe()`/`account_info()` touched the real RocketRide
  SDK) — the naming was just never fixed after the architecture pivot.
  Given the confusion it was causing, did the rename now instead of
  deferring further: new `src/contentmaster/draft_generator.py`
  (`DraftGenerator` class, same `draft_posts()`/`_llm_draft_posts()` logic
  verbatim, `run_pipe()`/`account_info()` dropped — genuinely unused).
  `pipeline.py`: `step3_rocketride_or_replay` -> `step3_generate_drafts`,
  audit event stage `"rocketride"` -> `"draft_generator"`. Verified live:
  a real run still produces grounded drafts, audit log shows
  `draft_generator/draft.start` and `.done`. `rocketride_client.py` left
  as dead code (unimported), same treatment as the other retired files.
- Also corrected an earlier suggestion: told the user to comment out the
  `source ~/.rote/shell/init.sh` line in `~/.zshrc` to stop Rote's
  agent-guard from blocking `rm`/`curl`/etc. (see docs/ERROR_LOG.md). User
  pointed out that line is already guarded by `[ -f ... ] &&`, so just
  deleting Rote entirely (`rm -rf ~/.rote ~/.local/bin/rote ~/.rote-play`
  — something they'd wanted done anyway, from the earlier hackathon-installs
  cleanup) achieves the same fix without editing a personal dotfile. Better
  answer; gave them that instead.

## 2026-09-13 (later still) — read-only audit, then cleanup pass

- User asked for a full read-only scan of files/code/packages for
  obsolete/trash/odd items before touching anything. Findings: `improve.py`
  (0 importers anywhere, fully superseded by `analysis.py`/`discuss.py`)
  and `rocketride_client.py` (already known, still on disk) are dead code;
  `config.py`'s `HydraDBSettings`/`RocketRideSettings`/`HotdataSettings`
  classes were still defined and wired into `Settings` with zero real
  consumers left; `.env`/`env.example` still carried ~15 stale
  `ROCKETRIDE_*`/`HYDRADB_*`/`HOTDATA_*` vars; `.claude/rules/rocketride.md`,
  `.cursor/rules/rocketride.mdc`, `.rocketride/docs/*` describe the fully
  retired RocketRide SDK; `warehouse/models/staging/stg_plays.sql`,
  `stg_events.sql`, and the `campaign_performance.sql` mart build fine via
  `dbt run` but have zero Python consumers (only `performance_history.sql`
  is read, by `analysis.py`); `plays/your-best-stress-reliever::*.json` and
  `metrics/post_metrics.jsonl` were leftover manual-test data; unused
  `Path` imports in `audit.py`/`metrics_store.py`. Also corrected an
  earlier false-positive from an over-hasty grep: `metrics_store.py` is
  NOT dead code (it's imported module-style — `from . import ...
  metrics_store` — which a `from .X import` grep pattern misses; it has 3
  real call sites in `pipeline.py`).
- User approved acting on most of it immediately, but explicitly said to
  leave the root-level hackathon artifacts (`Hackathon detail.pdf`,
  `Marketing hack white paper.pdf`, `pipelines/automarketer-content-gen.pipe`)
  alone until asked. Applied: trimmed the three dead settings classes (and
  the now-unused `_bool()` helper) out of `config.py`; stripped the stale
  vars from `.env`/`env.example`; removed the unused `Path` imports;
  cleared `metrics/post_metrics.jsonl`'s two test entries. `rm` is still
  blocked in this environment, so actual file deletions
  (`rocketride_client.py`, `improve.py`, the two `.claude`/`.cursor` rule
  files, `.rocketride/`, the two leftover `plays/*.json` test files) were
  handed back to the user as manual commands (see README.md's Project
  layout section). The unused dbt models and the root hackathon files were
  intentionally left as-is per the user's instruction.

## 2026-09-14 — starting Phase 1/9's external-CSV cross-validation

- Picked the external seed source for `warehouse/seeds/`: Google Trends
  "cat" search-interest over time — matches the `Demo-white paper/cat.rtf`
  demo product ("your-best-stress-reliever"), free/no-key CSV export, and
  is an actual time series (fits Phase 1's "news/current-events" intent
  better than a static reference table).
- No standalone `duckdb` CLI binary was installed yet (only the Python
  `duckdb` package via `dbt-duckdb`); recommended `brew install duckdb`
  for an interactive SQL shell to clean the raw export
  (`read_csv_auto(..., skip=N, header=false)` + type casts + `COPY ... TO`)
  instead of standing up MySQL — would've duplicated the DuckDB stack
  already in place for no benefit and broken the local-first/no-extra-server
  principle.
- Established the actual minimum schema a seed CSV needs to be useful: one
  date/time column at a joinable granularity (any name — gets aliased in
  the model, doesn't need to be called `ts`) plus at least one signal
  column; nothing else is required, and dbt doesn't enforce a shared
  schema across seeds.
- Clarified a scope question: the LLM (`discuss.py`) never reads the raw
  CSV. `analysis.py` queries DuckDB directly and hands discuss.py already-
  computed trend/correlation results as text — so CSV cleanliness is a SQL
  join-correctness concern (wrong join key/date grain -> silent NULLs),
  not something the LLM "figures out." Also flagged that sample size, not
  formatting, is the real limiter right now: `plays/_history.jsonl` only
  has a couple of real published posts, so any correlation computed
  against it won't be statistically meaningful yet regardless of how clean
  the CSV is — that needs more real runs accumulating over time, not a
  formatting fix.
- Status: CSV not yet seeded or joined — user is still preparing/cleaning
  the source file.

## 2026-09-14 (later) — Phase 1/9 external CSV: seeded, joined, verified live

- User supplied 2 raw Google Trends "related queries" exports
  (`webSearch_dog_cat_relatedQueries.csv`,
  `webSearch_dog_cat_RisingRelatedQueries_cat.csv`). Found via `diff` they
  were byte-identical — same "TOP" + "RISING" combined export saved under
  two filenames. Cleaned with DuckDB SQL (read each line as one column via
  `read_csv(..., delim='\x01')` since the file mixes section headers and
  2-column rows; located `TOP`/`RISING` markers by row number; split each
  line on its first comma; cast `TOP` scores to int and `RISING` growth to
  `(is_breakout bool, growth_pct int)`, handling the non-numeric
  `"Breakout"` value). Wrote the two results to
  `warehouse/seeds/google_trends_cat_related_queries_top.csv` and
  `..._rising.csv` — this also resolved the duplicate-file mixup, since
  each output now matches what its filename originally implied.
- User separately had already prepared 2 more clean CSVs themselves:
  `webSearch_cat_timeline.csv` (daily search interest, has a date column)
  and `webSearchcat_region_clean.csv` (interest by country, no date).
- Built 4 staging models, one per seed (`stg_google_trends_timeline`,
  `stg_google_trends_region`, `stg_google_trends_related_top`,
  `stg_google_trends_related_rising`). Only `stg_google_trends_timeline`
  has a per-day grain, so only it gets LEFT JOINed into
  `performance_history` (as `external_cat_search_interest`, matched on
  `trend.day = history.ts::date`) — the other 3 are snapshot/reference
  data with no join key, left as standalone tables meant for the
  draft/discuss prompt later, not the performance-history join.
- `dbt seed` initially errored on 6 unrelated files — root cause: the raw
  *uncleaned* Google Trends exports had been left inside
  `warehouse/seeds/Dirty_data/`, and dbt's `seed-paths: ["seeds"]` scans
  that whole tree recursively, not just top-level files, so it tried (and
  failed) to load the malformed multi-section raw files as seeds too.
  Fixed by moving that folder out of `seeds/` entirely, to
  `warehouse/Dirty_data_raw/` (`mv` still works in this environment even
  though `rm` is blocked) — dbt no longer sees it. **Lesson for next time
  any raw/messy source file needs to sit near a dbt project: never inside
  `seeds/` itself, even in a subfolder.**
- `dbt run` then built all 4 new staging models and `performance_history`
  successfully. Verified live by querying `performance_history` directly:
  every real row from today (`ts` on 2026-09-13) got
  `external_cat_search_interest = 51`, correctly matched from
  `webSearch_cat_timeline` — the join genuinely works end to end, not just
  compiles.
- Two pre-existing, unrelated `dbt run` errors surfaced during this same
  run — both are side effects of yesterday's test-data cleanup, not new
  breakage: `stg_plays` errors because `plays/*.json` now matches zero
  files (the two leftover test play JSONs were deleted per the user's
  cleanup request); `stg_post_metrics` errors because
  `metrics/post_metrics.jsonl` is now empty (cleared in that same
  cleanup) and DuckDB can't infer a schema from zero rows. Neither is
  fixed — confirmed (again) that nothing downstream depends on either
  model (`campaign_performance`, fed by `stg_plays`, was already flagged
  unused by any Python code in the 2026-09-13 audit; `stg_post_metrics`
  has zero downstream refs anywhere). Low priority; will self-resolve once
  a real play/metrics row exists again, or can be made tolerant of empty
  sources if that's worth doing before then.
- `analysis.py` was not touched yet at that point — it still only selected
  `ts, ctr, improvement_note` from `performance_history`, so
  `external_cat_search_interest` and the 3 reference tables weren't
  influencing any LLM output.

## 2026-09-14 (later still) — wired the new warehouse data into analysis.py/discuss.py

- User moved `Dirty_data_raw/` from `warehouse/` up to the project root
  (still gitignored — added the entry).
- `analysis.py`: `_query_history()` now also selects
  `external_cat_search_interest`; added `_query_external_signal()` (today's
  search interest, plus — once n>=4 published posts have it — a hedged
  above/below-average-interest-day ctr comparison, explicitly labeled
  "hint, not proof" given the tiny n) and `_query_trending_context()`
  (pulls top/rising related queries + top regions from the 3 reference
  staging tables). Both land on new `AnalysisResult` fields
  (`external_signal`, `trending_context`), included in the human-review
  printout and in `as_dict()` (so `capture_success()` stores them
  alongside the rest of the analysis verdict — no changes needed there,
  it already takes a generic dict).
- `discuss.py`: `_proposal_prompt()` appends both fields to the prompt
  when present, with an explicit "reference it only if genuinely
  relevant, don't force it" instruction so the model doesn't shoehorn a
  trending term into every post.
- Verified live end-to-end (not just unit-level): `analyze_performance()`
  against the real `Your best stress reliever`/bluesky history correctly
  computed `external_signal` (interest=51, n=2, "not enough data yet") and
  `trending_context` (8 top + 5 rising queries + 3 regions); then a full
  `synthesize_strategy()` call against the real local Ollama models showed
  both `llama3.2:3b` and `mistral:latest` independently picked up the
  RISING term "cat in the hat" from the trending context, unprompted, and
  worked it into their proposals — the judge's synthesis named it
  explicitly. This is the first real evidence the discuss step is actually
  using the external data, not just carrying it as inert metadata.
- Not done: no dbt tests yet (still open in SCHEDULE.md Phase 1); the
  `stg_plays`/`stg_post_metrics` empty-source errors from the prior entry
  are still unfixed (still low priority, still self-resolving).

## 2026-09-15 — dbt tests (Phase 1), run alongside user's own live testing

- User was manually testing `contentmaster run` end-to-end themselves
  (real Bluesky posts went out — `metrics/post_metrics.jsonl` picked up
  `draft-8613e798` and `draft-2fea8ca3` outside this session) and asked
  for other work to proceed in parallel, explicitly not touching anything
  that could interfere with that test. Added dbt tests only —
  `models/staging/_staging__models.yml` and
  `models/marts/_marts__models.yml`, purely new files, zero edits to any
  `.sql` model or any `src/contentmaster/*.py` file, so nothing in the
  actual `contentmaster run` code path changed.
- `dbt test` itself opens `warehouse/local.duckdb`, which
  `analysis.py`/`pipeline.py` also touch — deliberately ran it only once,
  confirmed via `ps aux` that no `contentmaster`/pipeline process was
  active at the time, to avoid a DuckDB single-writer lock conflict with
  the user's own concurrent test run. Did not run `dbt run`/`dbt test`
  again after that single check.
- 33 tests total, not-null + unique on the new Google Trends staging
  models, not-null on the existing staging models, not-null +
  accepted_values(`published`/`rejected`) on `status` for both marts.
  Result: 29 pass, 4 real findings (not test bugs):
  - `not_null_performance_history_ts`: 3 rows genuinely have a null `ts`
    — confirmed these are the first 3 rows of `plays/_failures.jsonl`,
    predating `ts` being added to `capture_failure()` (already documented
    in `stg_failures.sql`'s own comment as a known historical gap, not new
    breakage).
  - The 3 `stg_plays` not-null tests error (not fail) with the same
    "`plays/*.json` matches 0 files" IO error logged on 2026-09-14 —
    unchanged, still low priority, still self-resolving once a real play
    file exists again.
- Also fixed a dbt 1.12 deprecation warning in the new `_marts__models.yml`
  along the way (`accepted_values`'s `values:` needs to nest under
  `arguments:` now) — cosmetic, own new file only.

## 2026-09-15 (later) — analysis-timing design discussion (no code changed)

- User (pasting real analysis/discuss output from two live test runs)
  correctly diagnosed that `step6_track_metrics` pulls metrics seconds
  after publish (e.g. `impressions: 1, ctr: 0.0`) and
  `analyze_performance()`/`synthesize_strategy()` run on that same-instant
  reading — confirmed this is real, not a misreading: the two runs'
  `trending_context` blocks were byte-identical (same static Google
  Trends seed, fixed top-N query, nothing had refreshed between calls),
  and both discuss.py proposals converged on the same "Breakout" RISING
  term. Grepped `analysis.py`/`discuss.py`/`draft_generator.py` for the
  literal strings involved (`"cat in the hat"`, `"Bramerton"`) — zero
  hits, confirming this is a data/timing artifact, not hardcoding.
- User proposed a 3-checkpoint redesign (24-48h / 7 days / 30 days),
  citing real social-media-marketing practice around platform algorithm
  "發酵週期" (content needs time to be pushed to wider audiences before a
  verdict is meaningful) — recorded in full in docs/SCHEDULE.md Phase 9.
  Assessed as sound and consistent with the problem diagnosed above;
  flagged as needing: decoupling publish (fast) from analysis (delayed,
  scheduled), a due-for-review index, and a fallback rule for
  `draft_generator`'s `prior` lookup when the immediately-prior post
  hasn't reached its checkpoint yet. Not implemented — discussion only,
  per explicit request.
- Also discussed the `trending_context` staleness itself (separate from
  the timing issue): user proposed either changing the query or gating it
  on new CSV data arriving. Leaning toward comparing each round's query
  result against what's already stored in the last captured analysis
  (`plays/_history.jsonl`) and marking it "unchanged since last round"
  when identical, rather than literally gating on a CSV file landing
  (gating on the raw content matching is a more reliable signal of real
  novelty than gating on a human having dropped a new file in). Not
  implemented.
- Also asked whether "table"/"view" (from the dbt run log) can be renamed
  to avoid confusion — these are standard SQL/dbt materialization
  vocabulary (table = physically stored, view = computed at query time),
  not names this project invented; dbt's own CLI output can't be
  relabeled. Clarified the distinction vs. the project's own dbt *model
  names* (e.g. `stg_google_trends_timeline`), which are fully renameable
  since we chose them. Awaiting which one the user actually meant.

## 2026-09-15 (later still) — /grill-me design session for the 3-checkpoint redesign, Round 1

- Before writing any code for the analysis-timing redesign (SCHEDULE.md
  Phase 9), used the `/grill-me` skill to interview through the design as
  a decision tree instead of guessing. Found one hard fact first (not a
  decision — verified in code): `post_ref` (the platform reference
  `get_post_metrics()` needs to re-pull a post's numbers later) is
  currently held only in `pipeline.py`'s in-memory `results` list for that
  run's terminal summary — never written to disk anywhere. Confirmed
  `get_post_metrics(post_ref)` itself *is* re-callable later with just a
  saved reference (checked `platforms/base.py`/`bluesky.py`), so the
  redesign is technically feasible once that reference is persisted.
- Round 1 questions and decisions (full detail also in
  docs/SCHEDULE.md's Phase 9 entry):
  1. New file `plays/_tracking_review.jsonl` (append-only, same pattern as
     `_history.jsonl`/`_failures.jsonl`). Fields:
     `post_id, product, channel, post_ref, final_text, ts, reviewer_note,
     checkpoints_done`. `ts` renamed from an originally-proposed
     `published_at` to match every other JSONL file in this project.
     `reviewer_note` exists to survive into a `capture_success()` call
     that now happens much later, after the original in-memory `decision`
     object is gone. `checkpoints_done` prevents re-processing the same
     checkpoint every time the scheduled job runs.
  2. Immediate metrics pull at publish time is removed entirely, not kept
     for bookkeeping — writing to `_tracking_review.jsonl` *is* the "this
     really got published" record now.
  3. `capture_success()` (writes `plays/*.json` snapshot +
     `plays/_history.jsonl` ledger) moves out of the synchronous publish
     flow entirely, fires only from a completed checkpoint.
  4. Trigger: a new CLI command, manually run or user-scheduled via
     launchd/cron — not real automatic scheduling this iteration. User
     explicitly wants this labeled TBD in SCHEDULE.md (decided-for-now,
     not fully locked, revisit before/while building).
  5. Build order reversed from the original proposal: **24-48h checkpoint
     first, not 7-day** — user's reasoning: fastest way to prove the
     whole checkpoint/scheduling mechanism works in a real environment
     without waiting a full week. 7-day and 30-day follow once that's
     proven.
  Explicit consequence flagged (not yet a problem, just stated so it's
  not a surprise later): since `capture_success` only fires from a
  checkpoint, and 7-day is being built *after* 24-48h, `_history.jsonl`
  gets zero new entries until the 7-day checkpoint exists — so
  `draft_generator`'s already-decided fallback (find most recent post
  with completed 7-day analysis; else write ungrounded) will take the
  "write ungrounded" branch for every post during that gap.
- Round 2 opened (what the 24-48h checkpoint actually does now that it's
  the first piece of real infrastructure; CLI command shape; how "due"
  posts are found; whether `_print_run_summary()`'s output changes now
  that metrics aren't pulled synchronously) — not yet answered.

## 2026-09-15 (later still) — /grill-me Round 2

- Verified facts before answering: `human_loop.ReviewDecision` already has
  `edited: bool` + `reviewer_note: str` (holds "why edited" or "why
  rejected", depending on the decision) — so the user's request for a
  decision-type field + a free-text field was already satisfied by
  existing naming; recommended reusing `edited`/`reviewer_note` as-is in
  the new tracking file rather than inventing a `reviewer_comment` name,
  to avoid two different vocabularies for the same concept. Also verified
  `capture_failure()` already has a `reason` field — rejection reasons
  already have a home in `plays/_failures.jsonl`, unrelated to the new
  tracking file (which only ever holds *published* posts).
- User pushed back on the "24h is a light check, not a full
  analyze+discuss cycle" framing: proposed instead that **every**
  checkpoint calls `analyze_performance()`/`synthesize_strategy()`/
  `capture_success()`, each asking a different, appropriately-scoped
  question (24h: did the algorithm push this out at all; 7d: is the heat
  sustained; 30d: long-term/time-series). This is a real improvement over
  the original Round-1-proposed design — it resolves the "too early to
  judge success" problem by changing the *question* asked at each tier
  instead of skipping analysis at the earlier tiers. Consequence worked
  through: comparisons must stay within one tier (24h vs 24h, not 24h vs
  7d), so `analyze_performance()` needs a `checkpoint` parameter, and
  `plays/_history.jsonl`/`performance_history` need a `checkpoint` column
  since there's now one row per (post, checkpoint) instead of one per
  post.
- User also correctly flagged that `plays/*.json` being overwritten every
  `capture_success()` call would lose the 24h→7d→30d history for a single
  post now that it fires 3 times per post instead of once. Resolved: keep
  `plays/*.json`'s overwrite behavior as-is (it's meant to be "current
  state," not history) — the checkpoint-level history is exactly what
  `plays/_history.jsonl` already exists for, once it carries the new
  `checkpoint` field. Confirmed this reading was correct back to the
  user rather than assumed.
- `checkpoints_done` upgraded from a bare list to a dict mapping
  checkpoint name → the timestamp it actually ran, per user's request —
  lets a late-run checkpoint (the CLI command wasn't triggered exactly on
  schedule) be corrected for in analysis instead of silently treated as
  on-time.
- Confirmed: CLI command name `contentmaster review`; no upper bound on
  "due" (anything past threshold and not yet checkpointed is due,
  however late); `_print_run_summary()`'s changed text stays in English.
- New open question for Round 3: now that multiple checkpoint tiers can
  each independently complete an analysis for the same post, should
  `draft_generator`'s fallback prefer the highest-maturity tier available
  (7d over 24h) even when a shallower reading is more recent, or simply
  the most recent completed checkpoint of any tier? Not yet answered.

## 2026-09-15 (later still) — /grill-me Round 3, session complete pending confirmation

- Q10 decided: fallback prefers the highest-maturity completed checkpoint
  available (7d over 24h, 30d over 7d), not just whichever is most
  recent — reasoning already given (7d is the "golden verdict window",
  24h only answers a narrower question).
- User asked to also record a future idea: once there's real volume in
  both the 24h and 7d tiers, look into modeling the *relationship*
  between them — which early (24h) patterns predict a post that both
  explodes early and sustains through 7 days — to eventually weight
  `discuss.py`'s guidance, not just pick one tier over the other.
  Recorded in SCHEDULE.md as a future idea (needs data volume neither
  tier has yet — not actionable now).
- User asked a comprehension-check question, not a design change:
  confirmed `plays/*.json` is keyed by `(product, channel)`, so the same
  product posted to 2 different platforms gets 2 fully separate play
  files/histories — no cross-platform comparison exists today even if
  the same text was posted to both. Not changed, just confirmed as
  current, correct behavior; flagged as a possible future direction
  (comparing the same content's performance across platforms) but not
  something requested.
- No new open branches raised — Round 3 was the last item on the
  Round-2-generated frontier. Design session for the 3-checkpoint
  redesign is complete pending the user's explicit confirmation (per the
  grilling skill: no code gets written until that confirmation).
- User caught 2 mis-summarized points when reviewing the checkpoint
  summary: (1) the TBD label was on the wrong thing — `contentmaster
  review` as a manually-run CLI command is a settled decision, not TBD;
  what's actually undesigned is an *automatic* mode (deciding on its own,
  from each entry's `ts`, when to run itself, vs. the user or an external
  cron invoking it) — moved the TBD label there. (2) The fallback
  priority isn't a clean 3-tier ladder (24h < 7d < 30d) — 30d is a
  different *kind* of analysis (monthly aggregate/comparison across all
  posts, not a single-post reading), so it doesn't belong in the same
  "prefer the more mature single-post reading" comparison as 24h vs. 7d.
  Corrected `draft_generator`'s fallback to a 2-tier choice (7d over 24h
  only); how the 30d rollup feeds back into draft generation at all is
  now explicitly flagged as still open, deferred (doesn't block building
  the 24h checkpoint, which is next).

## 2026-09-15 (later still) — built and verified the 24h checkpoint

User confirmed the design (after the corrections above) and said to start
building. Full scope built in one pass, not just scaffolding:

- **New `src/contentmaster/tracking_review.py`**: `queue_for_review()`,
  `find_due()`, `mark_checkpoint_done()` — same "append + last-write-wins"
  read pattern `metrics_store.py` already uses (mirrored deliberately, not
  reinvented), writing `plays/_tracking_review.jsonl`. `checkpoints_done`
  is a dict of checkpoint -> the timestamp it actually ran (not a bare
  list), per the earlier "reduce deviation effect" decision — `find_due()`
  has no upper window, a late run still processes overdue posts.
- **`modiqo_play.py`**: `capture_success()`/`capture_failure()`/
  `_append_history()` all take a `checkpoint` param now. New
  `find_best_prior()` implements the decided 2-tier fallback (prefer a
  7d-tier entry over 24h, walking back through `plays/_history.jsonl` by
  product/channel — not just the literal most-recent post) — shaped to
  match what `draft_generator.py` already expects from `find_play()`, so
  no changes needed on that side beyond the one call-site swap in
  `pipeline.py`. Module docstring updated (was still describing the old
  immediate-capture flow and a stale "RocketRide" reference).
- **`analysis.py`**: new `CHECKPOINT_QUESTIONS` dict (24h/7d/30d's
  distinct questions); `analyze_performance()`/`_query_history()` take a
  `checkpoint` param and filter `performance_history` on it, so
  comparisons never mix tiers; the checkpoint + its question get prefixed
  into `evidence` (which `discuss.py`'s prompt already includes verbatim
  — no `discuss.py` changes needed, the checkpoint context flows through
  for free). `AnalysisResult` gained a `checkpoint` field.
- **`pipeline.py`**: docstring/flow description rewritten. Old
  `step6_track_metrics` + `step7a_analyze_and_discuss` +
  `step7_modiqo_capture` removed from `run()`'s synchronous path;
  replaced with `step6_queue_for_review()` (just writes the tracking-
  review entry, no metrics pull). New `_pull_checkpoint_metrics()` (same
  real-pull-with-mock-fallback logic the old step6 had, relocated),
  `step_checkpoint_analyze()` (one post, one checkpoint: pull -> analyze
  -> discuss -> capture -> mark done), `run_review()` (the
  `contentmaster review` entry point — finds due posts, processes each,
  handles `ReviewInterrupted` mid-batch same as the draft-review loop
  already did). `_print_run_summary()` no longer prints metrics/feedback
  (nothing was pulled synchronously to show) — prints a "queued for
  review, check back in 24h" notice instead, in English per the earlier
  decision.
- **`cli.py`**: new `contentmaster review [--checkpoint 24h|7d|30d]`
  subcommand (default `24h` — the only tier with real due-date logic
  behind it right now).
- **dbt**: `stg_success_history.sql` converted from `read_json_auto`
  inference to explicit `read_json(..., columns={...})` — empirically
  confirmed this was necessary, not just precautionary: a first `dbt run`
  attempt showed `checkpoint` didn't exist in the inferred schema at all
  (every existing row in `plays/_history.jsonl` predates the field, same
  root cause already documented for `ts` in `stg_failures.sql`, which
  already used this pattern and just needed `checkpoint` added to its
  existing explicit schema). `performance_history.sql` threads
  `checkpoint` through both sides of its `union all`.
- **Verified for real, not just "compiles"**: manually appended a
  backdated (`ts` 30h in the past) entry to `_tracking_review.jsonl` with
  `post_ref: null` (so metrics fall back to `mock_metrics()` — no live
  Bluesky call made during this test) and a throwaway product name, then
  ran the actual `run_review()` end-to-end: `find_due()` correctly picked
  it up; `_pull_checkpoint_metrics()` wrote a mock reading;
  `analyze_performance(checkpoint="24h")` printed
  `checkpoint=24h` in its header and the question in its evidence line;
  `synthesize_strategy()` made real calls to both local Ollama models,
  which picked up the Google Trends `trending_context` unprompted (same
  "cat in the hat" behavior as the 2026-09-14 test, confirming that
  wiring survived the refactor); `capture_success()` wrote `checkpoint:
  "24h"` into both `plays/*.json` and the new `_history.jsonl` row;
  `find_due()` run again correctly returned 0 (checkpoints_done
  deduplication works); `find_best_prior()` correctly retrieved the new
  entry. `dbt run`/`dbt test` both clean afterward except the 2
  pre-existing, already-documented, unrelated issues.
- Test data cleaned up afterward: emptied `_tracking_review.jsonl` (it
  only ever had the test entries), removed the one test line from
  `_history.jsonl` and `metrics/post_metrics.jsonl` (both otherwise had
  real rows, edited rather than wiped). One leftover file `rm` can't
  reach in this environment: `plays/test-checkpoint-product::bluesky.json`
  — handed to the user as a manual `rm` command, same pattern as every
  other blocked deletion this session.
- Not built yet, by design (see SCHEDULE.md): 7d/30d checkpoints, the
  automatic-scheduling mode for `contentmaster review` (still TBD), the
  30d-rollup feedback path into draft generation (still open), and no new
  dbt tests were added for the `checkpoint` column specifically (existing
  33 tests untouched).

## 2026-09-15 (later still) — real production test surfaced a content-variety bug

- User ran `contentmaster run` for real (not me — outward-facing publish
  decisions stay with the user) and rejected all 3 drafts: "this same
  content have been written" — confirmed a real, published post now
  exists (`plays/_tracking_review.jsonl` picked up `draft-a61d235d` for
  product "trouble shoot" with a real Bluesky `post_ref`), proving the
  checkpoint redesign works in a real run, not just the mock test.
- Diagnosed why drafts keep converging on the same content: `--product-name`
  only isolates a Cognee dataset, it doesn't change what gets extracted
  from the *same* `cat.rtf` file; `step1_cognee_extract()` always asks the
  same fixed query ("product name, key features, target ICP"); brand-new
  product names have no `prior` (cold start every time); llama3.2:3b at
  temperature=0.7 given near-identical prompts converges on similar
  phrasing, especially since the source document only has ~3-4 concrete
  facts to begin with (grounding rules forbid inventing more). Verified
  (not assumed) that "Your best stress reliever" — despite having real
  history — *also* hits cold start now: `find_best_prior()` correctly
  returns `None` for it, because (1) its old `plays/*.json` snapshot was
  deleted in an earlier cleanup and (2) its `_history.jsonl` rows predate
  the `checkpoint` field, which `find_best_prior()` deliberately skips
  rather than guess the maturity of.
- User's fix direction, larger than a prompt tweak: replace the single
  fixed-query extraction with a persistent, human-reviewed **topic
  index** — Cognee extracts a *list* of topics (brief one-liners, "so
  human doesn't need to spend a lot of time reviewing them"), a human
  reviews it once, it's stored locally so Cognee isn't re-queried (re-
  paying compute) every run, and each draft picks from the *least-used*
  topic(s) — tracked via a per-topic usage counter incremented after use
  — instead of a hardcoded "draft 1 covers X, draft 2 covers Y" template,
  which the user explicitly rejected as limiting variety and not scaling.
  This is also the fix for "the whitepaper's content hasn't been fully
  extracted and used" (declined swapping in a different whitepaper for
  now, for that reason) — one fixed Q&A query was always going to leave
  most of the source document untouched.
- Applied immediately (settled, no ambiguity): `draft_generator.py`'s
  temperature 0.7 -> 0.8. `discuss.py`'s separate temperature (also 0.7,
  different purpose — strategy synthesis, not creative writing) was left
  alone; not requested.
- User asked to design the topic-index pipeline through another
  /grill-me round before building, same as the checkpoint redesign.
  Round 1 opened — not yet answered.

## 2026-09-15 (later still) — topic-index /grill-me, decided and built

- Round 1 answered: index = `plays/{product}_topics.json`
  (`{topic, brief, used_count}[]` + `source_hash`), one per product
  (topics come from the product's own whitepaper, not per-channel).
  Cache invalidation = SHA-256 hash of the whitepaper file, compared each
  run — user asked whether hashing itself counts as "a Cognee scan"; it
  doesn't, it's a pure local file read, zero network/LLM cost, fully
  distinct from what we're trying to avoid re-paying for. Human review
  UX: batch review only when genuinely *new* topics appear (unchanged
  hash skips review entirely) — `[A]ccept all` as the fast default,
  `[c]hange some` for per-item `[e]dit`/`[d]elete`, plus a `[t]ype in`
  option the user specifically asked to add (Cognee extraction "will
  always be an unpredictable black box" without a way to add what it
  missed, not just correct/remove what it got wrong). Topic selection:
  least-`used_count`-first, ties broken by list order (reproducible, not
  random). Usage counting: only on successful *publish*, not at draft-
  write time — a draft rejected at human review means the wording didn't
  land, not that the topic itself should be considered "used up".
- Built in one pass:
  - **New `src/contentmaster/topic_index.py`**: `file_hash()`,
    `load_index()`/`save_index()`, `parse_topics_response()` (parses
    `"Topic: X | Brief: Y"` lines, skips anything that doesn't match
    rather than guessing), `_review_topics_interactive()` (the
    A/c/e/d/t flow, raises `ReviewInterrupted` on Ctrl+C/Ctrl+D like
    every other human gate in this project), `sync_topics()` (the hash-
    gate: zero Cognee calls when the whitepaper is unchanged; merges new
    topics into existing ones, preserving `used_count`, when it *has*
    changed), `pick_topics()`, `record_topic_used()`.
  - **`draft_generator.py`**: new `_llm_draft_posts_for_topics()` — one
    LLM call, one post per topic, each explicitly grounded in only its
    own topic's brief (prompt lists all N topics, asks for N lines back
    in the same order); returned drafts carry a `topic` field. Old
    `_llm_draft_posts()` (single shared context blob) kept as a fallback
    for when no topic index exists yet — extracted the shared
    "strip LLM preamble lines" logic both paths use into
    `_clean_llm_lines()` rather than duplicating it.
  - **`pipeline.py`**: `step1_cognee_extract` -> `step1_cognee_extract_topics`
    (asks Cognee to list topics instead of answering one fixed Q&A;
    delegates all the "was this actually needed" logic to
    `topic_index.sync_topics()`); `step3_generate_drafts` now takes the
    product's full topic list and narrows it to `n` via
    `topic_index.pick_topics()` before calling `draft_posts()`;
    `step6_queue_for_review` now also calls `record_topic_used()` when a
    published draft carries a `topic`. `run()`: a `ReviewInterrupted`
    during topic review now cleanly aborts the whole run (was at risk of
    being silently swallowed by the existing broad
    `except Exception: continue with no context` around step1 — split
    into its own `except ReviewInterrupted` first, consistent with how
    every other human gate in this pipeline stops on Ctrl+C rather than
    treating it as "an extraction failure, proceed anyway").
  - Temperature 0.7 -> 0.8 (from the prior conversation) already applied
    to `draft_generator.py`'s endpoint call before this build started.
- Verified for real, incrementally, not just at the end:
  - `parse_topics_response()` against a string with a deliberately
    unparseable junk line — correctly skipped it, kept the 3 valid ones.
  - `pick_topics()` against a hand-built 4-topic index with different
    `used_count`s — correctly picked the 2 least-used, confirmed
    stable tie-break order, confirmed repeats when `n` exceeds the
    topic count, confirmed empty-safe on `None`/`{}`.
  - `sync_topics()` end-to-end against a real temp file: first call hit
    the (mocked) extractor once and stored 2 topics; a second call
    against the *same* file made zero extractor calls (cache hit,
    verified via a call counter, not assumed); editing the temp file's
    content and calling again correctly re-extracted (counter went to
    2), correctly preserved the existing topic's `used_count`, and
    correctly added the new topic at `used_count=0`.
  - `draft_generator.draft_posts(topics=...)` against 3 real, distinct
    topics — a real Ollama call produced 3 genuinely different posts,
    each one visibly grounded in its own topic's brief and nothing else
    (this is the actual fix being verified, not just "the code runs").
  - Full integration through `pipeline.step3_generate_drafts` ->
    `step6_queue_for_review`: correctly picked the 2 least-used of 3
    seeded topics (skipping the most-used one), and after simulating a
    successful publish, the chosen topic's `used_count` in the on-disk
    index file went from 0 to 1 — confirmed by reading the file back,
    not just trusting the return value.
  - All test data (topic index files, `_tracking_review.jsonl` test
    lines) cleaned up afterward; the real entry from the user's own
    "trouble shoot" test run earlier this session was left untouched.
- `discuss.py` needed no changes — it already reads `analysis.evidence`
  verbatim, which already carries whatever grounding context the run
  used; nothing in the discuss/analysis layer cared how the draft's
  content came to exist.

## 2026-09-15 (later still) — real run surfaced 2 UX bugs; one fixed small, one fixed big

User actually ran `contentmaster run` for real (not me — outward-facing
publish decisions stay theirs) against a new whitepaper/product ("Fluffy
roomate"). Both bugs below were caught from that one real transcript, not
hypothetical.

- **Bug 1 — topic review's `t` silently did nothing.**
  `_review_topics_interactive()`'s top-level prompt was
  `if not choice.startswith("c"): return new_topics` — any input that
  wasn't "c..." (including "t") fell through to "accept all as-is". User
  typed "t" expecting the type-in flow; got silent accept-all instead.
  **Fixed** (user: "add a lock only a,c,t input. anything else shows
  error message"): the prompt now loops until the input is exactly "a",
  "c", or "t" — blank no longer defaults to accept, either; anything else
  reprints an explicit error and re-asks. "t" at the top level now jumps
  straight into typing a new topic (extracted the "type in" prompt into
  a shared `_prompt_new_topic()` so the top-level shortcut and the
  in-loop "t" option are the same code, not two copies). Verified
  non-interactively: invalid input and blank both correctly error and
  re-prompt; "a" correctly accepts.
- **Bug 2 — draft review's `[e]dit` took typed feedback as literal post
  text. A real broken post went live because of this**: reviewer typed
  "add some details" meaning it as an instruction; the old prompt just
  asked "New text:" and used whatever came back *verbatim* — the
  published post's entire text became the literal string "add some
  details" (`draft-e153c8d4`, live on the real Bluesky account,
  `https://bsky.app/profile/content-master-ai.bsky.social/post/3mvlqd3sjja2r`).
  Not reverted or edited on the live platform — that's the user's
  account/call, not mine to act on unasked.
  User chose the bigger fix ("大改") over just clarifying the prompt
  text:
  - **New `DraftGenerator.revise_post(name, current_text, feedback,
    brief="")`**: one LLM call, rewrites `current_text` applying
    `feedback`, still grounded in `brief` if given (the draft's topic
    brief, when it came from the topic index) — do-not-invent-new-claims
    kept as a rule. Returns `None` on failure so the caller can fall
    back, never raises.
  - **`human_loop.review_draft()` rewritten as a loop**, not a single
    input/return: prompt is now `[a]pprove / [e]dit / [f]eedback / [r]eject`
    (`[f]eedback` only offered when a `regenerate_fn` is passed).
    `[f]eedback` sends the typed text through `regenerate_fn`, shows the
    *revised* draft, and loops back to the same prompt — the reviewer
    sees what actually changed before committing to anything. `[e]dit`
    is now explicitly "the FULL replacement text, not instructions", and
    gained a confirmation step ("About to use this as the final
    text: ... Confirm? [y]es / [n]o, go back") before it commits — the
    same class of accidental-commit this whole bug was is now caught
    twice, not zero times. Input is locked the same way as topic review
    now (bug 1's fix applied here too, for consistency, not separately
    requested but the same defensive pattern): only the currently valid
    letters are accepted, anything else errors and re-prompts. On final
    approval after one or more feedback rounds, `edited=True` and
    `reviewer_note` joins the feedback given (so Modiqo/topic_index still
    get a real learning signal, not blank).
  - **`pipeline.step4_human_review`** gained a `product_name` parameter
    (needed for `revise_post`'s prompt) and binds `regenerate_fn` to the
    draft's own `topic`/`brief` via a closure — drafts from the old
    context/fixed-template path just get `brief=""`, still functional.
  - Verified non-interactively, including the *exact* failing scenario:
    fed `[f]eedback -> "add some details" -> [a]pprove` through a mocked
    `regenerate_fn` — confirmed the final `final_text` is the *revised*
    post (not the literal string "add some details"), `edited=True`,
    `reviewer_note="add some details"`. Separately verified the `[e]dit`
    confirm-step (declining goes back to the draft, not straight to the
    edit-reason prompt) and the strict input lock (invalid choice errors
    and re-prompts, matches bug 1's fix exactly).

## 2026-09-15 (later still) — corrected an inaccurate SCHEDULE.md claim; "what's next" check

- Before answering "what should we build next", checked whether earlier
  notes ("only 24h has real due-date logic wired to it — 7d/30d are
  accepted as flag values but nothing populates them as due yet") were
  still true, rather than assuming. They weren't: `tracking_review.find_due()`
  and `pipeline.step_checkpoint_analyze()` were never 24h-specific — both
  are generic over whatever `checkpoint` string is passed. Verified by
  backdating a test entry 8 days and running
  `contentmaster review --checkpoint 7d` for real: `find_due("7d")` found
  it, `analyze_performance(checkpoint="7d")` correctly asked the 7d-tier
  question, `capture_failure(checkpoint="7d")` tagged the result
  correctly. Corrected SCHEDULE.md. The real remaining gap for the
  original 3-checkpoint design isn't due-date detection, it's that 30d
  needs a genuinely *different-shaped* analysis (monthly aggregate across
  all posts, not `analyze_performance()`'s single-post-vs-tier-history
  comparison) — that part is still unbuilt.
- Also noticed (while re-reading the working files to verify the above)
  that the user has been running the pipeline for real in parallel with
  this session's work: 3 more real published "Fluffy roomate" posts in
  `_tracking_review.jsonl`, including 2 that used the new `[f]eedback`
  flow successfully in actual production use (`reviewer_note`: "instead
  take about indoor, talk outdoor this time", "testing the product") —
  first real confirmation the edit-flow fix holds up outside of this
  session's own tests, not just inside them.
- Test data from the 7d-checkpoint verification (`_tracking_review.jsonl`,
  `metrics/post_metrics.jsonl`, `plays/_failures.jsonl`) cleaned up
  afterward; the user's own real entries (including the 3 new ones found
  above) were left untouched.

## 2026-09-16 — Phase 4 image generation: chose a provider, built it, wired it in

- User wanted a LinkedIn post drafted about the hackathon build (drafted,
  not published anywhere — plain text handed to the user to post
  themselves) and, while working on it, decided image generation should
  come before finishing that post.
- Checked SCHEDULE.md's Phase 4 entry first rather than assuming it was
  designed: it was a single unexpanded line, "Image generation: FLUX.1
  schnell or SDXL Turbo, local" — no real design behind it yet.
- Checked this machine's actual specs before assuming "local" was still
  the right call: **8GB RAM, Apple Silicon (arm64)**, no `diffusers`/
  `torch`/ComfyUI/Automatic1111 installed, and Cognee (Docker) + Ollama
  (llama3.2:3b loaded) already competing for that same memory. A local
  FLUX/SDXL pipeline realistically would not run well here — this wasn't
  considered when the original one-line plan was written.
- Compared hosted options: Replicate (recommended first — same model
  already planned, hosted, ~$0.003/image, no free tier), fal.ai (similar
  to Replicate), Stability AI's own API (pricier), OpenAI gpt-image-1/
  DALL-E 3 (best quality, ~$0.02-0.08/image, most expensive by far),
  Google Imagen via Vertex (heaviest to set up). User asked specifically
  about Cloudflare Workers AI + FLUX.1-schnell — verified via live web
  search rather than relying on possibly-stale training knowledge:
  1024x1024 = 57.6 Neurons, free tier is 10,000 Neurons/day with no
  expiry (~173 free images/day), paid rate $0.011/1,000 Neurons beyond
  that (~$0.0006/image, cheaper than Replicate too). At this project's
  real volume (a handful of images per post), this is realistically $0
  forever — genuinely the better pick than the earlier Replicate
  recommendation, not just a different flavor of the same thing.
  Sources: developers.cloudflare.com/workers-ai/models/flux-1-schnell/,
  .../workers-ai/platform/pricing/, .../workers-ai/get-started/rest-api/.
- Verified the real integration surface before designing around
  assumptions: checked the installed `atproto` SDK's actual method
  signatures — `Client.send_image(text, image: bytes, image_alt: str,
  ...)` exists and is exactly what's needed; `Client.post()` (used until
  now) has no image parameter at all, confirming image support requires
  extending the `Platform` interface, not just adding a generator.
- Built: `src/contentmaster/image_generator.py` (`generate_image()`,
  Cloudflare REST call, returns `None` on any failure or missing config
  — never raises); `config.CloudflareSettings` (`account_id`, `api_token`
  with `repr=False`, `.configured` property matching the existing
  `BlueskySettings` pattern); `CLOUDFLARE_ACCOUNT_ID`/`CLOUDFLARE_API_TOKEN`
  added to both `.env` (left empty — real credentials are the user's to
  fill in, never done by me) and `env.example`; `platforms/base.py`'s
  `Platform.publish_post()` gained optional `image`/`image_alt` params
  (default `None`/`""` so a platform with no image support yet can just
  ignore them without breaking); `platforms/bluesky.py` implements it via
  `send_image()` when an image is given, falls back to the existing
  `post()` otherwise; new `human_loop.review_image()` — same
  brand-safety principle as `review_draft()`: nothing generated gets
  attached to a real post unreviewed. Since a terminal can't preview an
  image (that's literally Phase 5's reason for existing), it saves to a
  temp file, prints the path, and offers
  `[a]ttach`/`[r]egenerate`/`[s]kip`. New `pipeline.step4b_generate_image()`
  wired into `run()` right after text approval (grounds the image prompt
  in the *final* approved/edited text, not a draft that might still
  change) and before `step5_publish`, which now threads `image`/
  `image_alt` through to `platform.publish_post()`.
- Verified: `generate_image()` and the full `step4b_generate_image()`
  path both confirmed to degrade to `(None, "")` cleanly with no
  Cloudflare credentials configured (the actual current state — nothing
  breaks, a run right now still publishes text-only exactly as before
  this feature existed). `review_image()`'s full interaction loop tested
  with a mocked regenerate function: invalid input errors and re-prompts
  (same lock pattern as the 2 bugs fixed 2026-09-15), `[r]egenerate`
  produces new bytes and loops back to show them, `[a]ttach` returns the
  most recent (regenerated) bytes. All imports re-checked clean after
  every file touched.
- Not yet verified: an actual Cloudflare API call (needs the user's own
  account + API token, not configured yet) or a real image actually
  landing on a real Bluesky post — that's the next real-world check once
  credentials exist.

## 2026-09-16 (later) — real Cloudflare API call verified

- User added their own real `CLOUDFLARE_ACCOUNT_ID`/`CLOUDFLARE_API_TOKEN`
  to `.env` (their own action — credentials are never filled in by me).
  Confirmed `settings.cloudflare.configured` picks them up correctly.
- Called `image_generator.generate_image()` directly (isolated from the
  full pipeline, safe — just generates and saves locally, nothing
  published) with a test prompt. **Real success**: 494KB PNG returned
  from the actual Cloudflare API, saved and visually confirmed — a
  genuinely coherent, on-prompt image, not corrupted data or an error
  page. This is the first real (non-mocked) end-to-end proof the
  Cloudflare integration works, closing the "not yet verified against
  the real API" gap from the build entry above.
- Still not verified: an image actually attached to a real Bluesky post
  via `platform.publish_post(text, image=..., image_alt=...)` — the
  generation half now works for real, the publish-with-image half
  hasn't been exercised yet.

## 2026-09-16 (later) — image file was unfindable; fixed the save location

- Real bug: `review_image()`'s original `tempfile.NamedTemporaryFile()`
  saved into macOS's per-boot, per-user hidden temp folder
  (`/var/folders/.../T/`) under a random `tmpXXXXXXXX.png` name — a real
  reviewer couldn't relocate the file afterward.
- Fixed: now saves to a fixed, visible project folder
  (`generated_images/`, new `.gitignore` entry) named after the draft
  (`{draft_id}.png`) — same predictable path every time that draft's
  image is reviewed or regenerated. `review_image()` gained a `draft_id`
  parameter; `pipeline.step4b_generate_image()` passes `draft["id"]`
  through. Verified: saved to
  `generated_images/draft-test123.png` exactly as expected.
- User separately confirmed `contentmaster review`'s 7-posts-due output
  was not a bug — clarified that each due post gets its own full
  analysis block + human confirmation gate, processed one at a time, not
  batched; the pasted transcript was just post 1 of 7, still waiting on
  its confirmation prompt when copied.
- User is testing a real Bluesky post with an attached image themselves
  next — the "not yet verified" gap noted above may close from that,
  pending their report back.

## 2026-09-16 (later) — `contentmaster run` default posts 3 -> 1; open question raised on the analysis-confirm flow

- `where's the image?` — checked `generated_images/`: only the earlier
  test file was there. Explained why: the `contentmaster review` the
  user had just run processes *already-published* posts from before
  image generation existed, so none of those 7 had an image to begin
  with — image generation only fires inside `run()`, at draft-approval
  time. A real image will only appear once `contentmaster run` is run
  again post-fix.
- User raised (explicitly as "not sure", not a firm request) whether
  `analysis.py`'s current confirm-the-raw-analysis gate
  (`_confirm_with_human()`, right after `ANALYSIS ...`) should instead
  move to *after* `discuss.py`'s recommendation exists — one combined
  human review of metrics + recommendation together, rather than
  blessing the numbers before there's a recommendation to react to.
  Recorded as an open question in SCHEDULE.md's Notes section (not
  decided, not rejected) — the mechanics of what "disagree" does in the
  combined version (override note only vs. a regenerate loop like the
  draft `[f]eedback` flow) still need answering before building this.
- User also flagged, for whenever Streamlit gets built: today's CLI
  analysis output (raw dict/JSON-shaped text) is fine for testing but
  must not carry over as-is — Streamlit needs one clean line per post
  with its recommendation, not a dump of `AnalysisResult`'s fields.
  Noted for that future build, not applicable to the CLI today.
- **Changed `contentmaster run`'s default post count 3 -> 1** — reviewing
  3 drafts (each with its own text review, possible image review) every
  run was too much at this stage of testing, per the user. Still
  overridable: `contentmaster run --posts N`. Threaded through
  `pipeline.run(n_posts=1)` -> `step3_generate_drafts(n=1)` ->
  `DraftGenerator.draft_posts(n=1)` (all 3 defaults changed, not just the
  CLI flag, so calling any of them directly without an explicit `n` also
  gets the new default). Verified: `contentmaster run --help` shows the
  new `--posts` flag, default 1; imports clean.

## 2026-09-16 (later) — resolved the analysis-confirm open question: log both

- User answered the clarifying question directly: not option 1 (record an
  override note only) or option 2 (a regenerate loop like draft
  `[f]eedback`) — a 3rd design: **log the human's feedback *and* the
  LLM's current recommendation, and read both back when drafting the
  next post.**
- Restructured `analysis.py`: `analyze_performance()` no longer confirms
  internally — dropped its `interactive` param, now pure computation. The
  old `_confirm_with_human()` (private, ran on the raw verdict alone)
  became public `confirm_with_human(result, recommendation)`, which
  prints metrics *and* the recommendation together and asks once. Storing
  the human's disagreement note in `result.human_note` was unchanged —
  what changed is *when* this runs (after `discuss.py`, not before) and
  that the printed screen now shows the recommendation too, so a human
  saying "no" is reacting to something real, not blessing numbers in a
  vacuum.
- `pipeline.step_checkpoint_analyze()` reordered:
  `analyze_performance()` -> `synthesize_strategy()` -> `confirm_with_human
  (analysis, next_strategy)` — was: confirm analysis, then discuss,
  with no further human touchpoint on the recommendation itself.
- `modiqo_play.find_best_prior()` gained a `human_feedback` field, pulled
  from the stored play record's `analysis.human_note` — separate from
  `improvement_note` (the LLM's own suggestion), neither overwrites the
  other.
- `draft_generator.py`: both prompt-building paths
  (`_llm_draft_posts_for_topics` for the topic-index flow,
  `_llm_draft_posts` for the older context-blob fallback) now include
  `prior["human_feedback"]` alongside `prior["improvement_note"]` when
  present, with an explicit instruction to prioritize the human's
  direction where the two conflict.
- Verified in stages, not just at the end:
  - `confirm_with_human()` tested directly with a synthetic
    `AnalysisResult` + a recommendation string — confirmed the combined
    screen prints both, and disagreeing correctly sets
    `human_confirmed=False` / `human_note=<the typed feedback>`.
  - First attempted an end-to-end test through the real `run_review()`
    loop with a backdated synthetic entry — accidentally hit a *real* due
    post first (`draft-e28b0af0`) because enough real time had passed
    since the last entry; stdin ran out mid-way and raised
    `ReviewInterrupted`. Checked for side effects before continuing:
    confirmed `checkpoints_done` for that real post was untouched (the
    exception fires before any `capture_success`/`mark_checkpoint_done`
    call) — no corruption, just a wasted (harmless) metrics pull + 2
    discuss.py calls. Switched to testing `confirm_with_human()` and the
    `find_best_prior()`/`draft_generator` round-trip in isolation instead
    of fighting real due-post timing.
  - Full round-trip verified: `capture_success()` with a human note ->
    `find_best_prior()` returns both `improvement_note` and
    `human_feedback` correctly -> spied on
    `draft_generator.draft_posts()`'s actual prompt construction and
    confirmed both appear, in order, with the "prioritize the human"
    instruction between them.
- Test data (a synthetic "Test Feedback Roundtrip" play record, a
  backdated `_tracking_review.jsonl` entry) cleaned up afterward; the
  user's own real entries (including the interrupted-but-unharmed
  `draft-e28b0af0`, still correctly pending) were left untouched. Two
  more test-only `.json` snapshot files (`test-feedback-roundtrip::bluesky.json`,
  alongside the still-pending `test-checkpoint-product::bluesky.json`
  from earlier) can't be deleted in this environment (`rm` blocked) —
  handed to the user as a manual command, same pattern as every other
  blocked deletion this session.

## 2026-09-17 — full read-only code scan, then fixed via /grill-me with a red/yellow/green policy

- User asked for a full read-only scan (dead/obsolete/redundant code,
  bugs, unclear-purpose code) — no edits during the scan itself. Findings:
  - **Real, active bug**: `warehouse/models/staging/stg_plays.sql`'s
    `read_json_auto('../plays/*.json', union_by_name=true)` now also
    matches `topic_index.py`'s new `plays/{product}_topics.json` files —
    completely different schema, DuckDB's `union_by_name` genuinely
    errored (`Referenced column "channel" not found... Candidate
    bindings: "source_hash"`). Confirmed by actually running `dbt run`,
    not assumed.
  - **3 duplicate "make this safe for a filename" functions**:
    `topic_index._slug()` and `pipeline._dataset_slug()` were byte-for-
    byte identical regex logic (different fallback string only);
    `modiqo_play._play_key()` was a 3rd, weaker version — only
    lowercased + replaced spaces, left every other special character
    (commas, slashes, ...) untouched in a real file path.
  - Minor: `topic_index.py`'s `[e]dit or [d]elete` sub-prompt silently
    ignored invalid input (no error, unlike every other prompt in the
    same file — the exact class of bug fixed 2026-09-15 for the
    top-level a/c/t prompt, just missed in this one spot); `stg_plays.sql`'s
    comment still pointed at the pre-rename `src/automarketer/` path;
    `metrics_store.mock_metrics()`'s docstring didn't mention the
    checkpoint-time real-API-failure fallback case.
  - Reconfirmed, not new: `campaign_performance`/`stg_plays` still zero
    Python consumers; every previously-flagged pending-delete file is
    now actually gone (the user had run the manual `rm` commands from
    earlier sessions since the last check).
- User invoked `/grill-me` with a policy, not the usual open-ended
  design session: classify each finding red/yellow/green by *fix risk*
  (not bug severity) — green fixes go ahead immediately, no need to ask;
  red/yellow get the normal grilling treatment. Classified: the 3 minor
  fixes as green (mechanical, zero ambiguity) — fixed immediately, no
  round asked. The `stg_plays.sql` collision and the slugify
  consolidation as yellow/red (both involve a real design choice and/or
  touch how existing data is addressed) — grilled properly:
  - **Q1 (stg_plays collision)**: recommended moving topic index files
    to their own `plays/topics/` subdirectory rather than trying to
    make the glob itself smarter. User approved, adding: "make sure
    contentmaster review will connect to the right path" — checked by
    grepping every `topic_index` reference in `pipeline.py`: all live
    inside `run()`'s flow (`step1_cognee_extract_topics`,
    `step3_generate_drafts`, `step6_queue_for_review`), none inside
    `run_review()`/`step_checkpoint_analyze()` — `contentmaster review`
    was never coupled to this path and isn't affected either way.
  - **Q2 (slugify consolidation safety)**: user asked to check first,
    not assume. Extracted every distinct product name ever used across
    all real data files (`_history.jsonl`, `_failures.jsonl`,
    `_tracking_review.jsonl`, the topic index) — 7 total, only one with
    special characters ("Hairy, furry, cute little monster" — commas).
    Checked whether that product ever had a real `plays/*.json` snapshot
    (it hadn't — only ever appeared in `_failures.jsonl`, and
    `capture_failure()` never calls `_play_key()`) and separately
    confirmed *zero* `plays/*.json` snapshots existed at all at the time
    (established while fixing Q1) — so changing `_play_key()`'s
    behavior had zero risk of orphaning any existing file. Reported
    back; user said yes to merging.
- Built: new `src/contentmaster/slug.py` (one `slugify(text, default)`
  function); `topic_index.py`'s `_index_path()` now writes under
  `TOPICS_DIR = plays/topics/` and calls `slugify()` directly (its own
  `_slug()` removed); `pipeline._dataset_slug()` now calls `slugify()`
  (its own regex removed, `import re` dropped — no longer used anywhere
  else in the file); `modiqo_play._play_key()` now calls `slugify()` on
  *both* `product_name` and `channel` (previously only lowercased +
  replaced spaces, and never touched `channel`'s safety at all).
- Also handled directly (not part of the code-scan findings): removed 2
  stale test entries from `plays/_failures.jsonl` ("HydraDB Removal
  Test", "No Rocketride Package Test" — leftover from 2026-09-13
  verification runs, no longer relevant since those integrations are
  fully gone); left "Rename Test" alone (explained what it was — a
  same-day rename-verification artifact — but not asked to remove it).
  User flagged a period (`.`) might need adding to the slugify regex for
  some future file-naming case — noted, explicitly not acted on now.
- Verified in stages: `dbt run` confirmed clean of the collision error
  (back to the pre-existing, already-documented empty-glob state, not a
  new problem) after the Q1 move; the real "Fluffy roomate" topic index
  (4 topics) confirmed loading correctly from its new path; the comma-
  containing product name confirmed to now produce consistent,
  sanitized output after the Q2 merge; full import check and
  `contentmaster --help` both clean after every change.

## 2026-09-17 (later) — removed "Rename Test", documented the CTR formula

- Removed the "Rename Test" entry from `plays/_failures.jsonl` per the
  user (same class of stale 2026-09-13 verification artifact as the 2
  already removed — explained what it was first, then removed on
  confirmation).
- User asked how CTR is actually computed — recorded the exact formula
  in `docs/SCHEDULE.md`'s Notes section as a reference note (explicitly
  not a design idea): Bluesky exposes no impressions count, so
  `impressions = max(likes+reposts+replies, 1)`, `clicks = likes`,
  `ctr = clicks/impressions`. Every real checkpoint run reading
  `ctr=0.0` so far is correct — 0 real engagement on the test posts —
  not a calculation bug.

## 2026-09-17 (later) — Phase 5 (Streamlit review UI): /grill-me, then a real MVP

- Design session for Phase 5, via `/grill-me` (no arguments this time —
  proper multi-round interview, not the red/yellow/green risk framework
  from the earlier code-scan session same day).
- Established fact before asking anything: Streamlit reruns the whole
  script on every interaction — it has no way to *block* on input()
  the way the CLI's `human_loop.py` does, so `review_draft()`/
  `review_image()` can't be called by Streamlit directly. Core design
  direction: decouple generation from review via a queue (same pattern
  as the checkpoint system), Streamlit reads the queue instead of
  blocking inline.
- Round 1 decided: (1) generate draft + image together, eagerly, not
  the CLI's current 2-stage "text approved, then generate image" —
  Streamlit can't pause mid-review to generate reactively, and
  Cloudflare's free tier (~173 images/day) makes the wasted-generation
  cost of a since-rejected draft's image negligible. (2) User pushed
  back on "keep human_loop.py untouched, build Streamlit fully
  separate": correctly pointed out this was imprecise — clarified that
  only the *input-gathering* functions (`review_draft`/`review_image`,
  which call `input()`) can't be reused; `ReviewDecision` (the dataclass)
  and `draft_generator.revise_post()` (the feedback-driven rewrite) are
  plain data/logic with no I/O coupling and should be shared, not
  duplicated. (3) User overrode the earlier two-step-publish
  recommendation: approve = publish immediately, no second confirm —
  explicitly their call on their own account; a scheduled-publish
  feature (approve = schedule, not post) was raised as a real
  possibility but marked TBD, not designed now. (4) User asked to
  confirm understanding of "two paths" (CLI review inline in `run()` vs.
  a separate Streamlit command) — resolved as one shared generation
  code path with a `--review-ui cli|streamlit` flag (their earlier
  "option B"), not two independent generation implementations, so the
  two "paths" differ only in which interface reviews the result, not in
  how it's produced.
- User then asked for a *temporary MVP* of both invocation styles to
  actually try, explicitly scoped down: no publish needed yet, but
  draft + image generation must be real. Built a single-file prototype
  (`streamlit_app.py`, project root, not yet integrated into
  `pipeline.run()` or a queue file — deliberately a throwaway first
  step, documented as such in its own docstring) rather than the full
  queue+dual-entry-point architecture: a form (product/channel/
  whitepaper) triggers real `topic_index.pick_topics()` +
  `draft_generator.draft_posts()` + `image_generator.generate_image()`
  on click, shows the result, and offers Approve (stubbed — prints what
  *would* happen, no real publish) / Edit / Feedback (real call to
  `draft_generator.revise_post()`) / Reject.
  Guards against a real gap: if the cached topic index doesn't exist or
  its `source_hash` no longer matches the whitepaper file, the page
  shows a warning and tells the user to run `contentmaster run` in the
  terminal first — this MVP doesn't attempt to reproduce
  `topic_index.py`'s interactive a/c/t review flow in the browser (that
  also needs `input()` and has the same fundamental issue this whole
  session is about).
  `streamlit` installed into the venv, added as a new optional
  dependency group (`pip install -e ".[review-ui]"`) — not a core
  dependency, since this file is explicitly a prototype, not the final
  design.
- **Verified for real, in an actual browser, not just "the code looks
  right"**: launched the app (`streamlit run streamlit_app.py`),
  navigated Chrome to it, submitted the generate form for the real
  "Fluffy roomate" product (its topic index was already cached from
  earlier in this session) — got back a real draft ("Lifestyle" topic)
  and a real, genuinely on-topic generated image (an indoor/outdoor cat
  infographic — FLUX's usual garbled in-image text, but structurally
  correct and relevant). Clicked Feedback, typed "make it shorter and
  more playful", and confirmed the displayed text actually changed to a
  new, genuinely shorter/more playful LLM-revised version — the
  `revise_post()` round-trip through Streamlit's button-driven flow
  works for real, not just in the earlier CLI-only tests.
- Left the Streamlit server running (background) for the user to keep
  interacting with directly, rather than tearing it down after the
  scripted test.

## 2026-09-18 — Phase 5 real integration: queue file, `pipeline.run()` split, `streamlit_app.py` rewrite

- Replaced the throwaway MVP with the architecture confirmed across the
  `/grill-me` rounds. `contentmaster run` still does one shared
  generation path (extract topics, then generate drafts); only the
  review step now branches on a `terminal: bool` flag.
- New `draft_queue.py` (`plays/_draft_review.jsonl`, gitignored): the
  handoff point between generation and browser review. Same
  append-only, last write wins pattern as `tracking_review.py` and
  `metrics_store.py`. `queue_draft()` writes a pending entry,
  `find_pending()` reads every entry still pending in queued order,
  `update_entry()` merges new fields on top by appending a new line.
- `pipeline.py`: extracted `_extract_topics_and_generate()` (shared by
  both paths), `_run_terminal()` (the original inline review loop,
  unchanged), and new `_run_streamlit()`, which generates the image for
  every draft right away (so text and image are ready together when
  the browser page opens), saves each to
  `generated_images/{draft_id}.png`, queues the draft, then calls
  `launch_streamlit()`. `run()` picked up a `terminal` parameter,
  default `False`.
- `launch_streamlit()`: `subprocess.Popen(["streamlit", "run", ...])`
  with no `--server.headless` flag, so Streamlit's own default browser
  auto open behavior handles opening the page. No need to print a
  reminder to open it manually.
- `cli.py`: `contentmaster run` gained `--terminal` (`store_true`).
  Flipped from the earlier assumed default: no flags now opens the
  browser, `--terminal` opts into the original CLI review flow.
- `streamlit_app.py` fully rewritten. It no longer generates anything
  on the page. It reads `draft_queue.find_pending()`, shows the oldest
  pending draft (text plus image if one was generated), and offers
  Approve, Edit, Send feedback, Reject. Approve calls the real
  `step5_publish()` and `step6_queue_for_review()` immediately (approve
  equals publish now, per the user's explicit call: a later scheduled
  publish option is a separate, not yet designed feature). Edit updates
  the text in `session_state` for review before approving. Send
  feedback calls the same `draft_generator.revise_post()` already
  proven in the MVP. Reject calls `modiqo_play.capture_failure()`. Each
  action also appends the new status through `draft_queue.update_entry()`.
- Verified the wiring without a live browser session (the earlier
  background Streamlit launch had just been killed for low memory):
  confirmed `pipeline.py`/`cli.py`/`draft_queue.py` import cleanly,
  `contentmaster run --help` shows `--terminal`, `streamlit_app.py`
  compiles and every function it calls (`step5_publish`,
  `step6_queue_for_review`, `capture_failure`, `revise_post`) matches
  the real signatures in `pipeline.py`/`modiqo_play.py`, and ran a
  disposable test entry through `queue_draft` to `find_pending` to
  `update_entry` to confirm the full lifecycle behaves as expected. A
  full end to end test through an actual browser is still pending, left
  for the next round once memory pressure has eased.

## 2026-09-18 (later) — real analysis session on the first image post surfaces 5 real problems, all fixed

The first checkpoint analysis to ever complete on a real post with an
image (`draft-78918b42`, Fluffy roomate/bluesky) surfaced enough real
problems in a single read to be worth a full pass, not a patch. Traced
each one against real code and real audit log data before touching
anything, then fixed all five (see docs/SCHEDULE.md for the checklist
view):

1. **Engagement score formula was backwards.** The old
   `ctr = likes / max(likes+reposts+replies, 1)` punished a post for
   getting *more* positive engagement, since a repost or reply grew the
   denominator without growing the numerator. Replaced with a weighted
   average, `(w_like*likes + w_repost*reposts + w_reply*replies) /
   max(total, 1)`, bounded to the same interval regardless of total
   engagement. Weights (`pipeline.ENGAGEMENT_WEIGHTS`): like=1, repost=3,
   reply=2, reposts/replies worth more since they spread the post
   further or show a real conversation. `SUCCESS_CTR_THRESHOLD` moved
   0.02 -> 0.5 to match the new scale (0 = no engagement at all, any real
   engagement clears 0.5 with these weights). Still called `ctr`
   everywhere on purpose, the user wants one clean rename later once the
   metric actually settles, not a piecemeal one now.
2. **Checkpoint failures never fed forward.** `capture_failure()` only
   wrote to `plays/_failures.jsonl`; `find_best_prior()` (what grounds
   the next draft) only reads `plays/_history.jsonl`, which only
   `capture_success()` wrote to. Since Fluffy roomate had never once
   cleared the old threshold, every real recommendation this session
   generated for it was silently discarded, confirmed via the audit log:
   zero `improving_on_prior: true` events, ever, on any real product.
   Fixed: `capture_failure()` now also appends to `_history.jsonl` (new
   `outcome: "success"/"failure"` field) whenever a real checkpoint
   ran. Fixes two things at once: `find_best_prior()` can read a
   failure's improvement_note, and `analysis.py`'s `n_prior_posts`/trend
   finally see a chronically-failing product's real post count instead
   of reporting `no_history` forever. Propagated through the warehouse
   too (`stg_success_history.sql` now selects `outcome`,
   `performance_history.sql` carries it as `engagement_outcome`,
   distinct from `status`, which stays about publish vs. reject, not
   engagement). Verified live: a disposable failure record fed straight
   into `find_best_prior()`'s output, and showed up correctly in
   `performance_history` after `dbt run` (status=published,
   engagement_outcome=failure).
3. **Recommendations could read like a rebrand suggestion.** Nothing
   told the two discuss.py models the product name/brand is fixed, so
   "lean into the Cat in the Hat trend" could be misread as "become Cat
   in the Hat." Added an explicit line to both the proposal and (unused
   for now, see #5) judge prompts: product/brand is fixed, only a
   content theme or wording change is in scope.
4. **No way to specify who a post should actually reach.** The
   discuss.py recommendations kept citing Google Trends' generic top
   regions (Romania/Indonesia/Peru) as if they were real targeting
   advice, completely unrelated to this product's actual market. Added
   `--target-region`/`--target-audience` to `contentmaster run`
   (`topic_index.save_target`/`load_target`, stored per-product,
   partial updates only touch the field actually passed so one flag
   doesn't wipe the other out). Threaded into both draft_generator.py
   prompt paths (`_target_instruction`) and discuss.py's proposal prompt,
   which now explicitly prefers the real target over the trending
   context's regions when they conflict. Verified live against the real
   local LLMs: with `region=US` set, both models wrote about "cat owners
   in the US" unprompted and neither suggested a rebrand.
   Explicitly NOT built yet, noted for later per the user's own scoping
   call: the actual Google Trends fetch is still a fixed, ungrounded
   "top trend" pull, it doesn't filter by the target at all yet, and the
   target itself is single-dimension (region) for testing purposes only
   even though the user's real intent is broader (audience, age, gender,
   etc.). If the trend data ever grows richer tables (e.g. "country hot
   topic trend" joined by region as a foreign key), the DuckDB/dbt models
   will need real schema changes to support that, not just a Python-side
   filter. Flagged, not scheduled.
5. **The "third model judge" wasn't really a third model.** Confirmed:
   `DISCUSS_JUDGE_MODEL` defaults to `DISCUSS_MODEL_A`, so the "synthesis"
   pass was really model A judging its own proposal against mistral's,
   not independent third-party judgment, and this machine only has
   `llama3.2:3b`/`mistral:latest` pulled locally (checked via
   `ollama list`), no local third option, and this machine is already
   memory-constrained (8GB, a Streamlit background process got killed for
   it earlier today). User's call: skip building a real third-party judge
   for now, but don't delete the code. Added `_JUDGE_ENABLED` (env
   `DISCUSS_JUDGE_ENABLED`, default off) around the whole synthesis
   step; `synthesize_strategy()` now returns both raw proposals, labeled,
   instead of a fake merge, and logs a `discuss/judge_skipped` audit
   event so the skip itself is visible in the trail. Flip it back on
   once a real third-party model is wired up via `DISCUSS_JUDGE_MODEL`.

**Unrelated bug found and fixed along the way**: `topic_index.py` calls
`re.match(...)` in `parse_topics_response()` but never imports `re` —
verified it's a real, currently-live crash (`NameError`), not
theoretical. Never surfaced before because `_extract_topics_and_generate`
wraps the whole extraction in a broad `except Exception`, so it silently
degraded to the old fixed-template fallback instead of visibly failing;
confirmed via the audit log that this exact failure has never once fired
before today, meaning it's a recent regression (likely from one of this
session's earlier topic_index.py rewrites), not a long-standing issue
nobody noticed. Any brand-new or newly-changed whitepaper since then
would have silently skipped real topic-grounded drafting. Fixed with one
missing import line; verified live with a real call.

All five fixes plus the `import re` fix verified: every touched module
imports cleanly, `dbt run` on the two changed models succeeds and the new
`engagement_outcome` column shows up correctly in `performance_history`,
and a real end-to-end `synthesize_strategy()` call against the actual
local LLMs confirmed the target-region and judge-disabled behavior both
work together, not just in isolation.

## 2026-09-18 (later) — Phase 5 extended to `contentmaster review`'s human confirmation step

`analysis.confirm_with_human()` had the exact same blocking-`input()`
problem draft review had before Phase 5 — user asked for the same
treatment (browser by default, `--terminal` to opt out), explicitly
via `/grill-me` rather than just building it, since the framing in the
request ("mirroring Approve/Reject-with-feedback already built for
drafts") could be misread as confirm=success/disagree=failure, which
isn't how the real code works (see below).

**Key fact surfaced before asking anything**: `capture_success()` vs.
`capture_failure()` is decided purely by the real ctr threshold
(`SUCCESS_CTR_THRESHOLD`), completely independent of whether the human
Confirms or Disagrees — the human's choice only ever sets
`analysis.human_confirmed`/`human_note`, which get stored into whichever
capture call runs either way. This is a different shape than drafts'
Approve/Reject (there, the human's choice *is* the publish/capture
branch), so the two flows only look alike at a surface level.

`/grill-me` ran two rounds:
- **Round 1** (5 questions): (1) confirmed the eager pre-queue step runs
  metrics-pull + `analyze_performance()` + `synthesize_strategy()` up
  front, mirroring `_run_streamlit()`'s eager image generation, but
  *not* capture (deferred to decision time). (2) New question this
  session had to face that draft review never did: when does
  `tracking_review.mark_checkpoint_done()` fire? Decided: only at
  decision time, with a dedup check in the eager pass (skip a post
  already pending in the new queue) rather than marking done eagerly —
  marking done eagerly risked a post's checkpoint being permanently
  "handled" with no human decision ever recorded, if the browser session
  never got acted on. (3) Streamlit page structure: one process, `st.tabs()`
  splitting "Draft review" / "Analysis review", each tab reading its own
  queue file — rejected a single merged flat list (draft and analysis
  cards have genuinely different shapes) and rejected a fully separate
  second Streamlit app/port (unnecessary process/port complexity this
  same round's tabs approach avoids). (4) Confirmed "Disagree with a
  note" is single-shot, like `confirm_with_human()` already is — no
  re-loop back into another analysis round the way drafts' feedback
  loops back into another review round. (5) `review` gets its own
  `--terminal` flag (same name/semantics as `run --terminal`), and
  `launch_streamlit()` gets a lightweight port-probe (plain
  `socket.create_connection`, not a pidfile) so `run` and `review`
  opening a browser in the same session can't race to bind port 8501
  twice — also fixes a real pre-existing gap where a failed second
  `Popen` would fail completely silently (`stderr=DEVNULL`).
- **Round 2** (1 question, unlocked by round 1's dedup answer): does the
  dedup check apply only within one mode, or across terminal and
  streamlit invocations too? User picked cross-mode — checked once in
  `run_review()` before dispatching to either branch, since a
  browser-queued, still-undecided item could otherwise get processed a
  second time by a `--terminal` call before the human ever answers the
  first one.

**Built to match**, mirroring `run()`'s split exactly:
- New `analysis_queue.py` (`plays/_analysis_review.jsonl`, gitignored) —
  same append-only/last-write-wins pattern as `draft_queue.py`, keyed on
  `(post_id, checkpoint)` since one post has up to three separate pending
  analyses over its life (24h/7d/30d), unlike a draft's single `draft_id`.
- `pipeline.step_checkpoint_analyze()` split into `_pull_and_analyze()`
  (shared eager half), `_finalize_analysis()` (shared decision-time half:
  audit log, capture_success/failure per the ctr threshold, then
  `mark_checkpoint_done()`), `_run_review_terminal()` (pull+analyze, then
  the original inline `confirm_with_human()`, then finalize — unchanged
  behavior), `_run_review_streamlit()` (pull+analyze, then
  `analysis_queue.queue_analysis()` instead of blocking), and
  `apply_analysis_decision()` (rebuilds the `AnalysisResult` from the
  queued dict, applies the human's decision, finalizes, updates the
  queue entry) — called from Streamlit's Confirm/Disagree buttons.
- `run_review()` gained `terminal: bool` (default `False`) and the
  cross-mode dedup check (`analysis_queue.find_pending_for()`) before
  dispatching each due post; streamlit-mode entries call
  `launch_streamlit()` once after the whole due-list loop, same as
  `_run_streamlit()` does for drafts.
- `launch_streamlit()` now probes `localhost:8501` before `Popen`-ing a
  new process; shared by both `run()` and `run_review()`.
- `streamlit_app.py` gained `st.tabs(["Draft review", "Analysis review"])`
  — the draft tab is the untouched existing page; the new analysis tab
  reads `analysis_queue.find_pending()`, shows metrics + the
  recommendation, and wires Confirm/Disagree to
  `pipeline.apply_analysis_decision()`.
- `cli.py`: `review` subcommand gained `--terminal` (`store_true`, same
  help-text pattern as `run --terminal`).

**Verified for real**: every touched module imports and byte-compiles
cleanly; `contentmaster review --help` shows `--terminal`; a disposable
`(post_id="TEST-post-123", checkpoint="24h")` entry proved
`analysis_queue.py`'s full lifecycle end to end (`queue_analysis` ->
`find_pending` -> `find_pending_for` dedup check -> `update_entry` ->
no longer pending); then a second, more realistic disposable entry
(`product="ZZ_DISPOSABLE_TEST"`, ctr below threshold) was pushed through
the real `pipeline.apply_analysis_decision()` call — confirmed the real
audit events fired, `capture_failure()` really wrote to both
`_failures.jsonl` and `_history.jsonl` (the 2026-09-18-earlier
outcome-feed-forward fix from the other session today), and
`tracking_review.mark_checkpoint_done()` really recorded the checkpoint.
Left both disposable entries in place afterward rather than hand-editing
the append-only logs — same precedent as the "Streamlit Wiring Test"
entries already sitting in `_draft_review.jsonl` from the 2026-09-18
earlier Phase 5 session.

The cross-mode dedup test surfaced something worth noting rather than
hiding: setting it up (a due, backdated disposable post plus a pending
`analysis_queue` entry for it) also triggered `run_review()` to process
whatever was *actually* due first in iteration order — which turned out
to be a real, previously-untouched checkpoint for the real "Fluffy
roomate" post. That pulled real Bluesky metrics and ran real local-LLM
`discuss.py` calls before hitting `ReviewInterrupted` on the
non-interactive `input()` call. Confirmed this left no incorrect state —
capture and `mark_checkpoint_done()` both happen strictly after the
`input()` call that got interrupted, so the real post is still cleanly
due and will be picked up correctly next time the user actually runs
`contentmaster review`. The disposable dedup-test post itself was then
explicitly cleaned up (marked done + resolved) since, unlike
`_draft_review.jsonl`'s leftovers, an unresolved `_tracking_review.jsonl`
entry would otherwise resurface and print a `[skip]` line on every future
real `contentmaster review` invocation forever.

**Not yet verified**: an actual browser session against the new
"Analysis review" tab — the machine was down to ~73MB free physical
memory at verification time (`top -l 1`), the same class of constraint
that killed the earlier Streamlit MVP test process in the 2026-09-17
session, so it wasn't worth risking a live launch this round. Left for
next time once memory pressure eases, same as the still-pending "full
end to end browser test of the real queue-based flow" item already open
for the draft-review tab.

## 2026-09-19 — real usage bug: `launch_streamlit()`'s "already running" branch never opened a tab

User reported `contentmaster review` "doesn't open automatically."
Diagnosed against the actual running system rather than guessing:
`lsof -i :8501` showed a healthy Streamlit process already listening
(started the previous evening) with an active Safari connection —
cross-referencing timestamps (`ps -p <pid> -o lstart=` vs. the queued
`plays/_analysis_review.jsonl` entry's `ts`) showed they matched to the
second, and `SafariLaunchAgent` had started at that exact same moment.
So the *first* `contentmaster review` call genuinely had worked: Streamlit
launched, its own default-headless=false auto-open opened Safari, and
the analysis really was sitting there queued and pending.

The real bug: yesterday's Phase 5 review-side extension (see the entry
above) added a port probe to `launch_streamlit()` so a *second*
`run`/`review` call — server already up — wouldn't try to bind :8501
again. That branch only printed "already running, go to
http://localhost:8501" and returned; it never actually opened anything.
So the very first call auto-opened a real tab (correctly), but every
call after that (the common case once you've used the tool a couple of
times in one sitting) looked broken — nothing visibly happened.

Fixed by calling `webbrowser.open(url)` on *both* branches — only
whether a new server process gets spawned still differs by branch.
Verified for real against the actually-running server: called
`launch_streamlit()` directly, watched `lsof -i :8501` gain a brand-new
`Google` (Chrome) connection a second later, confirming a real new tab
opened against the existing server — not simulated, an actual browser
connection appeared.

## 2026-09-19 (later) — two more real usage bugs from actually using the browser review page

**Cognee wasn't running (Docker daemon down)**: user pointed `contentmaster
run` at a brand-new whitepaper (`Demo-white paper/cat.rtf`) and got back a
draft reading "How to Use a Cat — compound memory. Built for teams who
ship fast." — nothing about cats at all. Traced via `audit/events.jsonl`:
`cognee/extract.failed` with `Connection refused` on `localhost:8000`,
because Docker Desktop itself wasn't running (`docker info` failed) so
the Cognee container was never reachable. `_extract_topics_and_generate()`'s
broad `except Exception` degraded silently to `topics_all=[]`, so
`draft_generator.draft_posts()` fell back to its fixed-template path using
`run()`'s hardcoded default `features` list
(`["compound memory", "muscle-memory replay", "human-in-the-loop safety"]`)
— literally ContentMaster's own feature list, not the whitepaper's
content, with no CLI flag to override it. Confirmed this wasn't
theoretical: the resulting draft had already been approved and really
published to Bluesky (`draft-8347f25e`, `bluesky.posted`). Not a code fix
— told the user to start Docker Desktop and re-run
`scripts/configure_cognee_llm.sh`, and to decide whether to delete/edit
the already-live ungrounded post.

**Approve and publish never attached the generated image**: user asked
directly whether pressing Approve really publishes the image shown on
the page. Read the code rather than assuming: `streamlit_app.py`'s
Approve handler called `step5_publish(draft, text)` with no `image`/
`image_alt` argument at all, so it always defaulted to `image=None` —
confirmed for real against the same already-published `draft-8347f25e`:
its `bluesky.posted` audit event shows `has_image: False` even though
`generated_for_queue` right before it shows `has_image: True` — the
image was generated, shown, implicitly human-approved by being on the
page, and then silently dropped at the one step that mattered.
`platforms/bluesky.py`'s `publish_post()` itself was already correct
(`send_image()` when given bytes) — the gap was purely that Streamlit
never read `entry["image_path"]` back off disk. Fixed: Approve now reads
that file (if it exists) and passes the bytes + an `image_alt` (the
draft's `brief`, same convention as the terminal path's
`step4b_generate_image()`) into `step5_publish()`.

**Added Previous/Next navigation to both review tabs**: both tabs always
showed `pending[0]` — no way to look across several pending items before
deciding on one, which matters once more than one draft or analysis is
queued at a time. Added a small shared `_nav_index()` helper (a
session-state-backed index into the already-ts-sorted pending list, with
Previous/Next buttons and a "N of M pending" caption) used by both tabs.
Per-item state (text edits, expanded feedback/disagree boxes) was already
keyed by that item's own id, so paging back and forth doesn't lose any
in-progress edits.

## 2026-09-19 (later) — idle-timeout auto-shutdown for the Streamlit process

User asked whether closing the browser tab could auto-shut-down the
Streamlit server. Explained the real constraint before building anything:
a browser's `beforeunload`/`unload` event fires identically on a plain
page refresh, which this project's own review workflow does often (e.g.
to pick up a code change mid-session, exactly as happened earlier this
session) — wiring shutdown to that event would kill the server on a
refresh, not just a real close. Proposed an idle timeout instead (no
active session for N minutes -> self-terminate), which only reacts to a
sustained absence, not a momentary reconnect blip. User picked this over
leaving it as manual-only.

Implementation lives in `streamlit_app.py`: a daemon thread
(`_watch_idle_shutdown()`) polls `streamlit.runtime.get_instance()
._session_mgr.num_active_sessions()` every 20s (`_IDLE_CHECK_INTERVAL_SECONDS`),
and once that's stayed at 0 for 300s straight (`_IDLE_SHUTDOWN_SECONDS`),
sends itself `SIGTERM`. `_session_mgr` is a non-public Runtime attribute
— an explicit, agreed tradeoff for not needing a separate supervisor
process; the polling loop treats any exception from that internal API as
"can't tell, don't shut down" rather than crashing. Since Streamlit
re-executes the whole script file on every rerun (every interaction, for
every session), a plain module-level guard against starting the thread
twice isn't reliable — used `threading.enumerate()` to check real OS
thread state by name instead, which doesn't depend on how Streamlit's
script runner handles module-level state across reruns.

**Verified for real, not simulated**: copied `streamlit_app.py` with the
two timing constants dropped to 12s/2s, launched it standalone on a
throwaway port (8599, headless so the *server* wouldn't try to auto-open
anything on top of the manual step below), then used `open -a "Google
Chrome"` to open a real tab against it. Log showed `n_active=1` the whole
time the tab was open. Closed that specific tab for real via an
AppleScript `tell application "Google Chrome" ... close t` (matching by
URL) rather than killing the browser process — confirmed via `lsof` the
tab's TCP connection actually dropped. The watchdog's own log then showed
`n_active=0`, `idle_since` getting set, and — after the configured 12s of
continuous zero — the process printed "Stopping..." and genuinely exited
on its own, confirmed via `ps -p <pid>`. Cleaned up the throwaway copy
afterward; the real file keeps the 300s/20s production defaults, never
touched during the test.

**Real constraint surfaced by the test, documented rather than
papered over**: Streamlit doesn't execute the script body at all until a
session's first connection, so the watchdog thread doesn't exist until
someone has genuinely opened the page at least once. If `launch_streamlit()`'s
auto-open ever silently fails and nobody visits the page at all, this
particular safeguard never engages — accepted as a narrower, separately
already-visible failure (the printed fallback URL) rather than the
"opened once, then forgotten" case this feature targets.

## 2026-09-20/22 — The pipeline stops inventing data, and starts producing posts that differ

Two separate problems, found by asking why 12 real posts had taught the
project nothing. The data was untrustworthy; separately, it carried no
information. Fixing either alone would have left the other.

**Nothing invents a number any more.** `metrics_store.mock_metrics()` is
deleted. It returned plausible random engagement whenever a real reading
could not be taken, in exactly the shape of a real one, and every
fabricated-data incident traced back to it — a hand-deleted post was given
random likes, and a post that never published would have been too. A
failed pull now writes nothing and leaves the checkpoint due; the tracking
ledger refuses a post with no platform reference, at the write point
rather than by filtering at read time.

**The score can tell 1 like from 5.** It was a weighted *average*, so a
likes-only post scored exactly 1.0 however many likes it had. Now a
weighted sum plus two gates (`scoring.py`), and five states instead of a
success/failure binary. Tested against era 0's real distribution: 12 posts
at zero plus one like returns `hypothesis`, where the old absolute
threshold called 5 of 12 a success.

**Known limits are enforced, not discovered by failing.** A 322-character
draft was approved in the browser, rejected by Bluesky, silently degraded
into a "simulated publish", recorded as published, and queued to be
measured — the page said `Published.` and it was found two days later. The
limit had been declared in the adapter the whole time while four separate
prompts hardcoded "under 280 characters". Platform rules now live in one
place (`PlatformConstraints`) that generation, review and publishing all
read, and publish failures are classified rather than smoothed over.

**Posts can differ from each other.** All 12 were declarative cat-health
statements with zero questions, zero hooks, zero reposts, zero replies —
not chance. Topic selection happened before the model saw anything and was
imposed as an absolute grounding rule, while the improvement note was
appended hedged with "where it makes sense"; the absolute instruction won
every time, so prior evidence could never change what a post was *about*.
The model now chooses its topic and says why, claims must trace to the
whitepaper while framing is free, and a fixed share of every test group
must deliberately contradict the current best evidence — because wording
cannot stop an LLM treating a statistic as an order.

**Images.** A generated image came back as a slide of garbled pseudo-text
beside a cat photo. The prompt was the whitepaper's own sentence passed
through verbatim: a list of concepts with nothing visual in it, and a
diffusion model handed a sentence and no scene renders the sentence. Two
wrong fixes preceded the right one — a hardcoded "no words, no letters"
suffix (FLUX on Workers AI has no negative prompt, so every one of those
words went into the *positive* prompt), and an instruction that demanded
"the light" and "the mood", which produced the same warm window light in
every image. The model now writes the whole prompt, Python adds nothing,
and a check rejects prompts that name things which carry writing.

Three fixes in this session landed on the terminal review path and not the
Streamlit one, which is the default: the publish guard, the image prompt,
and image attachment. Worth suspecting first whenever something behaves
differently in the browser.

See `docs/SCHEDULE.md` Phase 10 for what is built and what is still open,
and Phase 11 for what was deliberately scheduled rather than built.

