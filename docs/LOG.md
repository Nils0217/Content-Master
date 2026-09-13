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
