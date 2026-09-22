# ContentMaster

Extract -> draft -> human review -> publish -> track real results -> improve
-> write to memory -> loop again. Originally built at a hackathon (as
"AutoMarketer.ai") against five mandated sponsor tools; now developed
independently. See `docs/WHITEPAPER.md` for the current architecture,
`docs/SCHEDULE.md` for what's next, `docs/LOG.md` / `docs/ERROR_LOG.md` for
daily progress and known issues.

## Install the CLI

```bash
./.venv/bin/pip install -e .
```

This registers `contentmaster` as a proper command (`./.venv/bin/contentmaster`,
or bare `contentmaster` once the venv is active — `source .venv/bin/activate`).
`pyproject.toml`'s `[project.scripts]` entry point is what does this;
`src/contentmaster/cli.py` is the dispatcher — add a new subcommand there as
the project grows (keep the actual logic in the relevant module, cli.py
stays thin). The old `run_pipeline.py` / `scripts/verify_bluesky.py`
entry points still work — they just forward to the same CLI now.

## Run it

```bash
contentmaster run --whitepaper "Marketing hack white paper.pdf" --channel x --product-name "AutoMarketer.ai"
```

First run extracts the whitepaper's *topics* (not one fixed Q&A answer,
see `topic_index.py`; re-extracted only if the file's content actually
changed since last time), reviews any genuinely new ones with you, then
writes one draft per least-used topic for real variety across runs
instead of the same reworded blob.

By default this opens a review page in your browser (Streamlit, see
`streamlit_app.py`): it shows each drafted post, generates its image if
`CLOUDFLARE_ACCOUNT_ID`/`CLOUDFLARE_API_TOKEN` are set (see
`image_generator.py`, skipped silently, text-only post, if not), and
lets you Approve (publishes immediately), Edit, Send feedback (the LLM
revises the draft from your notes instead of you typing the literal
replacement text, see `draft_generator.revise_post()`), or Reject.
Pass `--terminal` to review right there in the terminal instead (the
original flow, one draft at a time, same approve/edit/feedback/reject
choices plus an image review gate, `[a]ttach`/`[r]egenerate`/`[s]kip`,
before an image can be attached).

Once a draft publishes, `run()` stops there for it: **no metrics are
pulled and no analysis runs immediately**, a post seconds old has
nothing real to measure yet. It's queued (`plays/_tracking_review.jsonl`)
for the next step instead.

```bash
contentmaster review                 # checks the 24h checkpoint (default)
contentmaster review --checkpoint 7d # or 7d / 30d
contentmaster refresh                # re-read engagement for every live post under 30 days
contentmaster status                 # what needs a human right now
```

`refresh` (2026-09-21) re-reads engagement and nothing else — no analysis,
no model calls, no human decision — so it is safe to run as often as you
like. It exists because metrics used to be read exactly once, at the 24h
checkpoint, and then frozen forever: on a low-reach account likes arrive
days later, and the ledger recorded 1 like across 12 posts where the
account actually had 6. Posts found deleted are recorded as deleted and
never polled again.

`status` lists failed publishes, drafts and analyses waiting for you, and
which errors keep repeating. Every other command prints a one-line version
of the same summary at startup, and stays silent when there is nothing
pending.

Run this once real time has passed. It finds posts due for that
checkpoint, pulls their *real* metrics for the first time, then
`analysis.py` cross-validates against this product/channel's history *at
that same checkpoint tier* in the warehouse (`dbt run` in `warehouse/`
first — see below), and `discuss.py` has two different local models each
propose a next-round strategy (a third-model judge exists but is off by
default, see `discuss.py`'s `_JUDGE_ENABLED`).

By default this then opens the same browser review page as `contentmaster
run` (Streamlit, see `streamlit_app.py`), this time on its "Analysis
review" tab: real metrics + the actual recommendation together, with
Confirm / Disagree (with a note) buttons — one combined screen, not raw
numbers confirmed before there was even a recommendation to react to (see
`docs/LOG.md` 2026-09-16). Pass `--terminal` to confirm right there in
the terminal instead (the original `[Y]es / [n]o` flow, one post at a
time). Either way, if you disagree, your note is kept *alongside* the
LLM's recommendation, not in place of it — the next `contentmaster run`
for the same product+channel reads back the highest-maturity completed
checkpoint available (a 7d reading over a 24h one, even from an older
post) *and* both the recommendation and your feedback if you left any —
check the log for `"improving_on_prior": true`. That's the compounding
proof: content that gets better each round, based on real audience
response, not just cheaper. See `docs/WHITEPAPER.md` §2 for the full
diagram and docs/LOG.md 2026-09-15 for why this is split into two
commands instead of one, and docs/LOG.md 2026-09-18 for why the human
confirmation step also moved to the browser by default.

A due post already sitting in the browser queue awaiting your decision is
never re-pulled or re-analyzed by a second `contentmaster review`
invocation (in either mode) — it's skipped with a note telling you where
to go decide it, so the same post/checkpoint can't get double-captured.

Every event is written to `audit/events.jsonl` (one JSON line per step).

## Modules added 2026-09-20/22

| module | what it is for |
|---|---|
| `scoring.py` | the engagement score, the win/lose gates, and every tunable constant in one place |
| `publish_guard.py` | last check before anything reaches a real account — refuses text that is not a post, and enforces the platform's own limits |
| `publish_failure.py` | sorts a failed publish into content / environment / technical, because those need opposite responses |
| `product_registry.py` | says how often a product name has been used, and warns loudly when one has never been seen |
| `draft_labels.py` | what each draft IS and what it is TESTING, with a vocabulary that grows instead of fragmenting |
| `product_assets.py` | your own product images, preferred over generated ones |
| `error_index.py` | which errors keep happening — derived from the audit log, so they get prevented rather than handled |
| `pending.py` | what needs a human, surfaced in three places with different lifetimes |

`CONTENTMASTER_DATA_ROOT` redirects everything the pipeline writes
(`plays/`, `metrics/`, `audit/`, `generated_images/`) somewhere else, so a
test run cannot touch the real ledger. Inputs are still read from the
project root, and dbt always reads the real root, so test rows can never
reach a mart. Every command prints a banner while it is set.

## Platforms

Publishing/pulling-metrics is behind a common `Platform` adapter interface
(`src/contentmaster/platforms/base.py`) — nothing in `pipeline.py` or the
CLI is Bluesky-specific; `--channel <name>` works with whatever's
registered in `platforms/registry.py`.

```bash
contentmaster platforms              # list implemented + planned platforms
contentmaster connect bluesky        # auth check
contentmaster connect bluesky --search "AI agents" --limit 3   # + fetch + ingest into Cognee
```

**Implemented today:** Bluesky (official `atproto` SDK, app-password auth
via `BLUESKY_HANDLE`/`BLUESKY_APP_PASSWORD` — an **app password** from
bsky.app → Settings → App Passwords, never your account password; set in
the environment or `.env`, see `env.example` — never hardcoded, never
logged). `platforms/bluesky.py`'s exceptions all map to the generic
`PlatformConfigError` / `PlatformAuthError` / `PlatformRateLimitError` /
`PlatformAPIError` types from `platforms/base.py`, so any platform's
failures look the same to calling code.

**Planned, not implemented yet** (see `docs/SCHEDULE.md`): Mastodon, X,
Instagram, Facebook, YouTube. Adding one: write `platforms/<name>.py`
implementing the `Platform` interface, register it in
`platforms/registry.py`'s `PLATFORMS` dict — no CLI or `pipeline.py`
change needed.

Verified live (Bluesky): auth succeeded, real posts fetched, published for
real, and real engagement (likes/reposts/replies) pulled back after
publish.

## Project layout

```
.env                         # all credentials/config (gitignored)
product_assets/<product>/     # your own product images (gitignored). When this folder
                               # has images, they are used instead of generating one —
                               # a real photo beats an approximation. Least-used first.
pyproject.toml                # packaging + the `contentmaster` CLI entry point
requirements.txt
run_pipeline.py               # deprecated — forwards to `contentmaster run`
scripts/
  configure_cognee_llm.sh     # (re)start Cognee wired to Ollama
  start_hydradb.sh            # unused now (HydraDB removed 2026-09-13) — kept only in case a future
                               # LanceDB/Phase 2 migration wants the old graph as reference
  verify_bluesky.py           # deprecated — forwards to `contentmaster connect bluesky`
src/contentmaster/
  cli.py                       # `contentmaster` CLI dispatcher — thin, no real logic of its own
  config.py                   # loads .env into typed settings
  slug.py                      # one shared slugify() for filenames/dataset names — 2026-09-17,
                               # consolidated from 3 near-duplicate versions (see docs/LOG.md)
  cognee_client.py             # layer 1
  document.py                   # 2026-09-19 — reads a source document's plain text LOCALLY
                               # (.rtf/.txt/.md, and .pdf if the optional `pypdf` is installed).
                               # Shared by cognee_client's ingestion and by the draft-generation
                               # fallback that runs when Cognee is down, so both see the same text
  topic_index.py                # 2026-09-15: the whitepaper's topics as a reviewed, cached,
                               # usage-tracked list — replaces one fixed Cognee Q&A per run
  platforms/                    # Platform adapter interface + implementations
    base.py                       # the Platform ABC + Post + generic exceptions
    bluesky.py                    # first implementation
    registry.py                   # name -> Platform class; PLATFORMS / PLANNED_PLATFORMS
  metrics_store.py             # layer 3 — local JSONL, read by warehouse/ dbt (hotdata.dev retired)
  draft_generator.py            # layer 4 — calls local Ollama directly (renamed from
                               # rocketride_client.py 2026-09-13, see docs/LOG.md — it never
                               # actually depended on RocketRide's cloud service). One post per
                               # topic (see topic_index.py); revise_post() powers human_loop.py's
                               # [f]eedback option
  human_loop.py                 # brand-safety gate (draft review) — [a]pprove / [e]dit (literal
                               # text, confirmed before it commits) / [f]eedback (LLM revises from
                               # your notes) / [r]eject. review_image() applies the same gate to
                               # generated images (2026-09-16, see image_generator.py)
  image_generator.py            # 2026-09-16 (Phase 4) — Cloudflare Workers AI / FLUX.1-schnell.
                               # generate_image() returns None (never raises) if
                               # CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN aren't set — text-only
                               # publish, same as before this existed
  tracking_review.py            # 2026-09-15: plays/_tracking_review.jsonl — publish just queues a
                               # post here; no metrics/analysis until contentmaster review, later
  draft_queue.py                 # 2026-09-17 (Phase 5): plays/_draft_review.jsonl — the handoff
                               # between draft+image generation and the browser's "Draft review" tab
  analysis_queue.py              # 2026-09-18 (Phase 5 extended to review): plays/_analysis_review.jsonl
                               # — same handoff, for a completed analysis awaiting a human decision;
                               # keyed on (post_id, checkpoint), not post_id alone
  analysis.py                    # ANALYSIS: cross-validate vs. warehouse history, *scoped to one
                               # checkpoint tier* (24h/7d/30d — see pipeline.py). confirm_with_human()
                               # is still the terminal-mode confirmation; the browser path calls
                               # pipeline.apply_analysis_decision() instead, see analysis_queue.py
  discuss.py                      # ANALYSIS -> DISCUSS: 2 local models propose, 1 synthesizes
  modiqo_play.py                # layer 5 (muscle memory) — snapshot + append-only history ledger,
                               # both now tagged with which checkpoint tier produced them
  audit.py                      # JSONL audit log
  pipeline.py                   # orchestrator — run() (extract -> draft -> review -> publish ->
                               # queue) and run_review() (checkpoint -> analyze -> discuss -> log);
                               # both default to a shared Streamlit page (launch_streamlit() dedupes
                               # a second launch via a port check), --terminal on either command
                               # keeps the original blocking-input() flow with no browser at all
streamlit_app.py                 # the browser review page both commands open by default — a "Draft
                               # review" tab (draft_queue.py) and an "Analysis review" tab
                               # (analysis_queue.py), same process/port either way
plays/                        # captured muscle-memory patterns: _history.jsonl (real timeline),
                               # *.json (per product+channel current-state snapshot),
                               # _tracking_review.jsonl (published, awaiting a checkpoint),
                               # {product}_topics.json (the topic index), _failures.jsonl,
                               # _draft_review.jsonl / _analysis_review.jsonl (pending browser review)
audit/events.jsonl            # audit log
warehouse/                    # dbt + DuckDB (+ MotherDuck) analytics — see warehouse/README.md
docs/                          # WHITEPAPER.md, SCHEDULE.md, LOG.md, ERROR_LOG.md
```

2026-09-19 (code scan): this section used to list files to delete by
hand. Every one of them is already gone — `rocketride_client.py`,
`improve.py`, `.claude/rules/rocketride.md`, `.cursor/rules/
rocketride.mdc`, `.rocketride/`, and the `test-checkpoint-product`
snapshot, along with the earlier batch (`src/automarketer/`,
`hydradb_client.py`, `hotdata_client.py`, the original
`your-best-stress-reliever::*.json` test snapshots). Nothing here needs
deleting; the instructions themselves were the stale thing.

Two scripts do remain and are still referenced above:
`scripts/configure_cognee_llm.sh` (live — how Cognee gets an LLM) and
`scripts/start_hydradb.sh` (dead since HydraDB was removed 2026-09-13,
kept only as a reference for a possible future graph migration).

## One-time environment setup (already done on this machine)

```bash
bash scripts/configure_cognee_llm.sh   # Cognee container wired to local Ollama
```

Requires Ollama running locally with `llama3.2:3b` and `nomic-embed-text`
pulled (`ollama pull llama3.2:3b && ollama pull nomic-embed-text`). A
smaller model (`llama3.2:1b`) responds faster but was unreliable at
structured-output extraction in testing — Cognee's summarization step kept
failing Pydantic validation and retrying with exponential backoff. `3b` was
the smallest model that passed a structured-output benchmark reliably; it
still fits comfortably alongside Docker on an 8GB machine.

---

## (historical) AutoMarketer.ai — hackathon MVP (2026-09-11)

> Everything below this line is the original hackathon-day README content,
> kept as a historical record — not updated for the later rename
> (`automarketer` → `contentmaster`) or platform-adapter refactor. Command
> examples below use the old names and won't work as typed; see the
> sections above for current usage. Some tools mentioned (RocketRide,
> HydraDB, hotdata.dev) are no longer part of the go-forward plan — see
> `docs/WHITEPAPER.md` §3.

Working loop from the white paper:

```
Cognee.ai → HydraDB → RocketRide.ai → [human review] → (publish)
   → hotdata.dev → Modiqo.ai (Rote) → write back to HydraDB
```

All six sponsor tools are **live and verified**, not stubbed:

| Layer | Tool | Status |
|---|---|---|
| Memory construction | Cognee.ai | ✅ Self-hosted Docker, LLM = local Ollama (`llama3.2:3b`) + `nomic-embed-text` — no external API key, works offline. `add()`→`cognify()` on the real whitepaper PDF completes in ~90s and produces a real graph/summary. |
| Memory storage | HydraDB | ✅ Local graph-node (Docker). Writes/reads verified via real Cypher; data persists to `hydradb-data/store`. |
| Live query/analytics | hotdata.dev | ✅ CLI authenticated, workspace "marketing hack", instant database `automarketer` with table `automarketer.public.post_metrics`. Every simulated publish appends a real row (`databases load --append`) and reads it back with a real `hotdata query`. |
| Orchestration/motion | RocketRide.ai | ✅ `pipelines/automarketer-content-gen.pipe` is a real, hand-authored pipeline (Webhook → RocketRide Wave agent [tools: Cognee + HydraDB, LLM: local Ollama, memory: internal] → Guardrails → response) — validated against the RocketRide VS Code/Cursor extension's own schema docs and **actually run** via the SDK: `client.use()` + `client.send()` returned `completedCount: 1, failedCount: 0, warnings: [], errors: []` — real HydraDB Cloud database (`default-tenant`) wired in too. |
| Muscle memory | Modiqo.ai (Rote) | ✅ CLI logged in, hackathon warm-up play passed, local workspace `automarketer` registers each successful run. |
| Security | Snyk | ✅ CLI authenticated. `snyk test` (deps): 0 vulnerabilities. `snyk code test` (source): caught a LOW path-traversal pattern in `config.py`, fixed by resolving+confining the path to the project root. |

### Bluesky integration (as first built)

Fetches public Bluesky posts (official `atproto` SDK, app-password auth) and
feeds them into the same Cognee ingestion pipeline the whitepaper goes
through — a second real input to the knowledge graph, not a separate system.

```bash
# 1. connection test only
automarketer bluesky verify

# 2. + fetch public posts + ingest into Cognee
automarketer bluesky verify --search "AI agents" --limit 3
```

Requires `BLUESKY_HANDLE` and `BLUESKY_APP_PASSWORD` (an **app password**
from bsky.app → Settings → App Passwords, not your account password) in the
environment or `.env` (see `env.example`) — never hardcoded, never logged.
`src/automarketer/bluesky_client.py` maps `atproto`'s exceptions to clear
`BlueskyConfigError` / `BlueskyAuthError` / `BlueskyRateLimitError` /
`BlueskyAPIError` types so missing creds, bad auth, and rate limits each
fail with a distinct, readable message instead of a raw traceback.

Verified live: auth succeeded, 3 real posts fetched, and
`CogneeClient.add_raw_texts()` accepted them
(`status: PipelineRunCompleted`) — same `/api/v1/add` endpoint
`add_document()` already used for the PDF, just called with `raw_data`
strings instead of a file.

(This was later refactored behind a generic `Platform` adapter interface
so Bluesky is one of several eventual destinations rather than a
special-cased path — see the "Platforms" section above.)

### RocketRide: how the `.pipe` file was actually built

Not via GUI drag-and-drop in RocketRide Cloud's canvas (the mouse-precision
approach kept missing connector hit-targets). Instead:

1. Installed the real RocketRide VS Code extension into Cursor:
   `cursor --install-extension RocketRide.rocketride` (works — Cursor is
   VS Code-compatible; there's no `code` CLI on this machine).
2. Opening this folder in Cursor made the extension drop
   `.rocketride/docs/*.md` into the workspace — the authoritative pipeline
   JSON schema, component config patterns, and layout rules, straight from
   the tool itself (see `ROCKETRIDE_COMPONENT_REFERENCE.md`,
   `ROCKETRIDE_PIPELINE_RULES.md`).
3. Fetched the live component catalog via the SDK (`client.get_services()` /
   `get_service(name)`) to get exact provider names and config schemas for
   `webhook`, `agent_rocketride`, `llm_ollama`, `memory_internal`,
   `tool_cognee`, `db_hydradb`, `guardrails`, `response_answers`.
4. Hand-wrote `pipelines/automarketer-content-gen.pipe` as plain JSON
   against that schema (`.pipe` files are portable JSON — this is a
   supported, documented way to build one, not a hack).
5. Ran it for real: `client.use(filepath=...)` → `client.send(token, ...)`
   → `client.get_task_status(token)` showed `completedCount: 1,
   failedCount: 0`. Once a real HydraDB Cloud database + API key were
   added to `.env` (workspace already existed: database `default-tenant`,
   found at dashboard.hydradb.com/databases), a re-run with the old
   pipeline instance `terminate()`'d first came back with **zero warnings,
   zero errors** — all five sponsor tools genuinely load-bearing in one
   pipeline.

`pipelines/services-catalog-reference.json` (gitignored — regenerate with
`client.get_services()`) has the full catalog if you want to extend the
pipeline further.

### What's still simulated (be upfront about this if a judge asks)

- `step5_publish()` posts for real on `--channel bluesky` (gated by the
  human_loop approval); every other channel still simulates — no adapter
  wired for X/LinkedIn/etc. yet.
- hotdata's underlying engagement numbers are simulated except when a real
  Bluesky post exists (then step6 pulls real like/repost/reply counts) —
  the storage/query round trip through hotdata.dev itself is 100% real
  either way, not mocked.

### Known rough edges / next steps (as of hackathon day — see docs/SCHEDULE.md for current)

1. `hydradb-data/` has a few smoke-test nodes mixed into the real graph
   from connection verification (a `Product{id:101}` node with feature
   "fast"). Harmless, but wipe `hydradb-data/store` and restart the
   container for a clean graph before the real demo if it bothers you.
2. Cognee's Ollama summaries are serviceable but rough (small local model);
   swap `LLM_MODEL` in `.env` for a hosted model (OpenAI/Anthropic — see
   `scripts/configure_cognee_llm.sh`) if quality matters more than staying
   fully offline.
3. ~~`db_hydradb` needs a real HydraDB Cloud database + API key~~ — done
   (`.env` has both; database `default-tenant`). Note this is a
   **separate** HydraDB from the local self-hosted graph-node the rest of
   the project uses directly via `hydradb_client.py` — two different
   HydraDB instances, both real. `pipeline.py`'s
   `step3_rocketride_or_replay()` still calls the local `draft_posts()`
   stand-in rather than this `.pipe` — wire `RocketRide.run_pipe()` in
   when ready to switch over.
4. **Fixed (was a real bug):** `step1_cognee_extract()`'s return value used
   to be silently dropped in `run()` — every draft was generated from the
   fixed fallback feature list, never from what Cognee actually extracted
   from the uploaded document. Also: `.rtf` files 500'd (no server-side
   loader installed) and every run shared one `automarketer` Cognee
   dataset, so a second run's search results bled into the first's. Fixed
   all three: `.rtf` gets converted client-side (`striprtf`) and ingested
   via `add_raw_texts()`; each product now gets its own Cognee dataset
   (`pipeline._dataset_slug()`); the extracted text is threaded through to
   `RocketRide.draft_posts(..., context=...)`, which calls the local LLM
   to write posts grounded in it (falls back to the old template if Cognee
   has nothing). Verified against `Demo-white paper/cat.rtf` — drafts now
   reference the document's actual content ("environmental enrichment",
   "mutual trust", "Play, feeding, and interaction"), not generic copy.
5. Promote a proven `plays/*.json` into a released Rote play:
   `rote play pending save automarketer` → inspect the emitted
   `rote play template create ...` command → QA → `rote play release`.
6. Go post "ready & warmed up" in the hackathon Discord (Rote's own
   checklist item — manual, needs your Discord login).
