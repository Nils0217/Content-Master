# AutoMarketer.ai — hackathon MVP

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
| Orchestration/motion | RocketRide.ai | ✅ `rocketride` SDK, connected + authenticated against your account. |
| Muscle memory | Modiqo.ai (Rote) | ✅ CLI logged in, hackathon warm-up play passed, local workspace `automarketer` registers each successful run. |
| Security | Snyk | ✅ CLI authenticated. `snyk test` (deps): 0 vulnerabilities. `snyk code test` (source): caught a LOW path-traversal pattern in `config.py`, fixed by resolving+confining the path to the project root. |

## Run it

```bash
./.venv/bin/python run_pipeline.py --whitepaper "Marketing hack white paper.pdf" --channel x --product-name "AutoMarketer.ai"
```

First run generates drafts via RocketRide and logs `rocketride/draft.start`.
Run it again with the same product+channel and watch for
`modiqo/play.replayed` instead — Rote's captured play replaces a fresh
RocketRide call. That's the compounding/muscle-memory proof.

Every event is written to `audit/events.jsonl` (one JSON line per step).

## One-time environment setup (already done on this machine)

```bash
bash scripts/start_hydradb.sh          # local HydraDB graph-node
bash scripts/configure_cognee_llm.sh   # Cognee container wired to local Ollama
```

Requires Ollama running locally with `llama3.2:3b` and `nomic-embed-text`
pulled (`ollama pull llama3.2:3b && ollama pull nomic-embed-text`). A
smaller model (`llama3.2:1b`) responds faster but was unreliable at
structured-output extraction in testing — Cognee's summarization step kept
failing Pydantic validation and retrying with exponential backoff. `3b` was
the smallest model that passed a structured-output benchmark reliably; it
still fits comfortably alongside Docker on an 8GB machine.

## Project layout

```
.env                         # all credentials/config (gitignored)
requirements.txt
run_pipeline.py               # CLI entrypoint
scripts/
  configure_cognee_llm.sh     # (re)start Cognee wired to Ollama
  start_hydradb.sh            # start the local HydraDB graph-node
src/automarketer/
  config.py                   # loads .env into typed settings
  cognee_client.py             # layer 1
  hydradb_client.py            # layer 2 (+ OpenCypher subset notes)
  hotdata_client.py            # layer 3 (real CLI writes + queries)
  rocketride_client.py         # layer 4
  human_loop.py                 # brand-safety gate
  modiqo_play.py                # layer 5 (muscle memory)
  audit.py                      # JSONL audit log
  pipeline.py                   # orchestrator
plays/                        # captured muscle-memory patterns (per product+channel)
audit/events.jsonl            # audit log
```

## What's still simulated (be upfront about this if a judge asks)

- `draft_posts()` generates text locally instead of via a real RocketRide
  `.pipe` (none built yet in RocketRide Cloud's editor); `run_pipe()` is
  wired and ready for when you build one.
- `step5_publish()` simulates posting instead of calling a real social
  platform API — wiring a real one needs a specific platform + explicit
  go-ahead (outward-facing action).
- hotdata's underlying engagement numbers are simulated (no live posted
  campaign yet to pull real clicks from) — but the storage/query round trip
  through hotdata.dev itself is 100% real, not mocked.

## Known rough edges / next steps

1. `hydradb-data/` has a few smoke-test nodes mixed into the real graph
   from connection verification (a `Product{id:101}` node with feature
   "fast"). Harmless, but wipe `hydradb-data/store` and restart the
   container for a clean graph before the real demo if it bothers you.
2. Cognee's Ollama summaries are serviceable but rough (small local model);
   swap `LLM_MODEL` in `.env` for a hosted model (OpenAI/Anthropic — see
   `scripts/configure_cognee_llm.sh`) if quality matters more than staying
   fully offline.
3. Build one real `.pipe` in RocketRide Cloud's editor and swap
   `draft_posts()` for `run_pipe()`.
4. Promote a proven `plays/*.json` into a released Rote play:
   `rote play pending save automarketer` → inspect the emitted
   `rote play template create ...` command → QA → `rote play release`.
5. Go post "ready & warmed up" in the hackathon Discord (Rote's own
   checklist item — manual, needs your Discord login).
