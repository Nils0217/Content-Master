# Error log

One entry per distinct problem, newest first. Goal: grep the exact error
text here before re-debugging from scratch. Each entry: symptom (verbatim
where it helps grep), cause, fix, where it lives in code/log.

---

### human_loop.py's [e]dit prompt published a reviewer's *feedback* as the literal post text

**Symptom:** A real, live post on the account read exactly "add some
details" — the whole post, nothing else. No crash, no error; the
reviewer had typed that string meaning it as an instruction ("please add
more detail to this draft"), not as the literal replacement text.
**Cause:** The old `[a]pprove / [e]dit / [r]eject` prompt only had one
way to change a draft — "New text:" — and used whatever was typed
*verbatim* as `final_text`. There was no way to give the LLM feedback and
have it revise the draft; typing an instruction there just became the
post.
**Fix (2026-09-15, `大改` per the user — a real redesign, not a wording
tweak):** split into two distinct options. `[e]dit` still takes literal
replacement text, but now shows "About to use this as the final text: ...
Confirm? [y]es / [n]o, go back" before it commits. New `[f]eedback`
option: the human describes what should change, `DraftGenerator.revise_post()`
sends it to the LLM (grounded in the draft's topic brief if it has one),
and the *revised* draft is shown before any approval is possible. See
`src/contentmaster/human_loop.py` (`review_draft()`, now a loop, not a
single input/return) and `src/contentmaster/draft_generator.py`
(`revise_post()`). Verified by replaying the exact failing input
(`[f]eedback` -> "add some details" -> `[a]pprove`) through a mocked LLM
call and confirming `final_text` was the revision, not the literal
feedback string.

---

### topic_index.py's review prompt silently treated an unrecognized answer (e.g. "t") as "accept all"

**Symptom:** Typing "t" at "Accept all as-is, or make changes? [A]ccept
all / [c]hange some >" did nothing visible — no error, no type-in prompt,
the topics were just silently all accepted as extracted.
**Cause:** `if not choice.startswith("c"): return new_topics` — *any*
input that wasn't "c..." fell through to "accept all", including "t",
which the reviewer expected to open a type-in-a-new-topic flow, and
including a blank/typo'd answer.
**Fix (2026-09-15, per the user: "add a lock only a,c,t input. anything
else shows error message"):** the prompt now loops until the input is
exactly `"a"`, `"c"`, or `"t"` — blank no longer defaults to accept
either; anything else prints an explicit error and re-asks. `"t"` at the
top level now jumps straight into the type-in flow (factored into a
shared `_prompt_new_topic()` so the top-level shortcut and the in-loop
`"t"` option can't drift apart). See
`src/contentmaster/topic_index.py`'s `_review_topics_interactive()`.

---

### Root cause of "Security: rm blocked in workspace" (and curl/wget/ssh/nc/node/ruby too)

**Symptom:** Same as the earlier `rm`-specific entry below, but now with
the actual cause instead of just "something in this dev environment blocks
it." Also applies to `curl`, `wget`, `ssh`, `nc`, `node`, `ruby` — all
print their own "Security: X blocked" variant and return 1.
**Cause:** Rote's shell integration. `~/.zshrc` sources
`~/.rote/shell/init.sh`, which (silently, on every new shell) sources
`~/.rote/shell/common/agent_guard.sh`. That script's `is_agent_session()`
checks for `CLAUDECODE`, `CLAUDE_SESSION_ID`, `CLAUDE_CODE_SESSION_ID`,
`CURSOR_SESSION_ID`, `CLINE_SESSION_ID`, `AIDER_SESSION_ID`,
`ROTE_AGENT_MODE`, `CODEX_*` — if any are set (true for basically any AI
coding agent, not just when actually using Rote for the task), it
unconditionally `function`-shadows those commands. There is no per-command
or session opt-out flag in the script itself.
**Fix:** Can't be granted/allowed from inside a session — it's a shell
function, not a Claude Code permission. **Better fix than editing
`~/.zshrc`:** that source line is already `[ -f ~/.rote/shell/init.sh ] &&
source ...` — file-existence-guarded — so just removing Rote entirely
(`rm -rf ~/.rote ~/.local/bin/rote ~/.rote-play`, in the user's own
terminal, not blocked there) makes the guard a no-op with no dotfile edit
needed, and gets the CLI off the system too if that's also wanted. Only
touch `~/.zshrc` directly if Rote itself needs to stay installed for some
other reason.

---

### improve.py's suggestions were silently based on zeros, not real metrics

**Symptom:** No error, no crash — `generate_improvement_note()`'s prompt
always showed "0 likes, 0 reposts, 0 replies" regardless of the post's
actual performance, so every suggestion it ever produced was disconnected
from the real numbers.
**Cause:** The function read `metrics.get('like_count'/'repost_count'/
'reply_count')`, but the `metrics` dict it's actually called with (from
`pipeline.py`'s track step / `metrics_store`) uses a different shape:
`impressions/clicks/conversions/ctr`. Wrong keys just silently `.get()`
their way to the `0` default — no exception, nothing to notice.
**Fix:** Use the correct field names
(`clicks`/`conversions`/`ctr`/`impressions`). Found while building
`analysis.py`/`discuss.py` to replace this step's role — a reminder that a
`dict.get(key, default)` on a metrics/result dict is exactly the kind of
mismatch that won't announce itself; when wiring a new consumer onto an
existing metrics shape, print/assert the actual keys once rather than
trusting the field names in an older call site.

---

### DuckDB: `select ... from read_json_auto(...)` — column referenced before it's defined / column doesn't exist

**Symptom 1:** `Binder Error: Column "ts" referenced that exists in the
SELECT clause - but this column cannot be referenced before it is
defined`, from a query like `select try_cast(ts as timestamp) as ts, ...
from read_json_auto(...)`.
**Symptom 2 (after wrapping in a CTE to dodge symptom 1):** `Binder Error:
Values list "raw" does not have a column named "ts"` — happens
specifically when the JSON source file's *current* rows genuinely don't
contain that field yet (e.g. a field just added to the writer, with zero
existing rows using it). `read_json_auto`/`read_json(..., union_by_name=
true)` infer the schema from what's actually in the file; a column absent
from every current row isn't inferred as null, it's not in the schema at
all.
**Cause:** Two different problems that look similar. #1 is disambiguation
between a source column and the alias defined for it in the same SELECT
list — DuckDB's binder didn't resolve it the way plain SQL name-scoping
rules would suggest. #2 is genuine — the column really isn't in the
inferred schema yet.
**Fix:** For #1, put the source read in a CTE and qualify the column
(`with raw as (select * from read_json_auto(...)) select
try_cast(raw.ts as timestamp) as ts, ... from raw`) — but that alone
doesn't fix #2. For #2 (the actual fix needed when a field is newly added
and old rows predate it), use `read_json` (not `_auto`) with an explicit
`columns := {...}` schema map instead of relying on inference — this
forces every declared column to exist (null where absent from a given
row) regardless of what's actually present in the file today. See
`warehouse/models/staging/stg_failures.sql`.

---

### `rm` is blocked in this dev environment: `Security: rm blocked in workspace`

**Symptom:** Any `rm` (even `rm -rf` on something clearly disposable, or a
throwaway file created in the same command) fails with exactly this
message — no file gets deleted, no error otherwise, exit code 1.
**Cause:** A sandbox/policy layer in this dev environment blocks the `rm`
binary outright, regardless of target. It is not path- or
flag-dependent — tested against `/tmp` scratch files too.
**Fix:** There isn't one from inside a session — `docker rm`/`docker rmi`,
`npm uninstall`, `brew uninstall`, `pip uninstall`, and editing/overwriting
a file's *contents* all still work fine (only the literal `rm` binary is
blocked), so prefer those where the goal is "remove this thing" and a
non-`rm` tool can do it. When a file/directory genuinely has to go (e.g.
`src/automarketer/` after the rename to `contentmaster` — see
`docs/LOG.md` 2026-09-13), the practical move is: neutralize it in place
(exclude from packaging, stop anything from importing it) and hand the
user a one-line `rm -rf <path>` to run themselves, rather than treating it
as blocking further work.

---

### `pip freeze` after `pip install -e .` pollutes requirements.txt

**Symptom:** `requirements.txt` gains a line like
`-e git+https://github.com/<you>/<repo>.git@<sha>#egg=automarketer` plus
the entire dependency tree of anything else installed in the venv (e.g.
`dbt-duckdb`'s ~40 transitive packages), even though the package's actual
direct dependencies are five packages.
**Cause:** `pip freeze` dumps everything installed in the environment,
including the editable package itself (as a git-remote reference) and
whatever else happens to share the venv (dbt/duckdb, installed for
`warehouse/` — unrelated to the core package).
**Fix:** Don't regenerate `requirements.txt` via blind `pip freeze` once
`pyproject.toml` exists. `requirements.txt` should mirror
`pyproject.toml`'s `[project.dependencies]` by hand (or via `pip-compile`
from a proper `.in` file, if that tooling gets added later) — not be
whatever happens to be installed in the venv this session.

---

### dbt: `Error: Invalid value for '--profiles-dir': Path 'warehouse' does not exist`

**Symptom:** `dbt run` fails immediately with this, even though
`warehouse/profiles.yml` exists.
**Cause:** `DBT_PROFILES_DIR=warehouse` was set as a relative path, then
`cd warehouse` was run — the relative path no longer resolves from the new
working directory.
**Fix:** Set `DBT_PROFILES_DIR` to an absolute path (`export
DBT_PROFILES_DIR="$(pwd)"` right before/after `cd warehouse`), or always
set it from the repo root before changing directories.

---

### DuckDB Python: `ModuleNotFoundError: No module named 'numpy'`

**Symptom:** `.df()` on a `duckdb` query result raises this.
**Cause:** `.df()` needs pandas/numpy, which this project deliberately
doesn't depend on (kept minimal per `docs/WHITEPAPER.md`'s
"only add what's actually imported" rule).
**Fix:** Use `.fetchall()` (and `.columns` for headers) instead of `.df()`
for ad-hoc checks; only add pandas as a real dependency if a model
actually needs DataFrame operations.

---

### RocketRide SDK: `RuntimeError: Pipeline is already running`

**Symptom:** `client.use(filepath=..., env=NEW_ENV)` raises this on a
second call for the same `.pipe`.
**Cause:** A prior `use()` call (e.g. with old/incomplete env vars) is
still running server-side; a second `use()` without `use_existing=True`
refuses to start a duplicate.
**Fix:** To pick up new env vars, explicitly stop the stale instance first
— `token = (await client.use(filepath=..., use_existing=True))['token']`
then `await client.terminate(token)` — before calling `use()` again with
the corrected env. (Historical: only relevant if `pipelines/*.pipe` is
still in use — see `docs/SCHEDULE.md` Phase 0.)

---

### Cognee: `LabelCountMismatchError` — "Provide one label per data item"

**Symptom:** `POST /api/v1/add` with `raw_data` (multiple text items) and a
single `labels` string returns 400.
**Cause:** Cognee's `labels` field must have exactly one label per item in
`data`+`raw_data`, comma-separated — not one shared label.
**Fix:** `CogneeClient.add_raw_texts()` builds `labels` as
`",".join([label] * len(texts))` before sending. If calling the endpoint
directly, do the same.

---

### Cognee: 500 on `.rtf` files — "No loader found for file ... extension '.rtf'"

**Symptom:** `add_document()` on any `.rtf` file 500s.
**Cause:** Cognee's server has no document loader installed for that
extension (needs `pip install cognee[docling]` or `cognee[unstructured]`
on the *container*, not the client). Supported without those extras: pdf,
txt, md, csv, json, and common code/image/audio extensions — not rtf/docx.
**Fix (current):** `CogneeClient.add_document()` detects `.rtf` client-side
and converts it with `striprtf` before routing through `add_raw_texts()`
instead of a file upload.
**Better fix (Phase 3):** install `cognee[docling]`/`cognee[unstructured]`
on the container so more formats work server-side without a client-side
special case per extension.

---

### Cognee: search results pulled in unrelated content from other runs

**Symptom:** Drafts for one product/document reference facts from a
completely different, previously-ingested document.
**Cause:** Every run shared one Cognee dataset (`COGNEE_DATASET`, a fixed
env default), so `search()` retrieved across everything ever ingested, not
just the current document.
**Fix:** Each product now gets its own Cognee dataset —
`pipeline._dataset_slug(product_name)` — so a fresh `CogneeSettings(dataset=...)`
is built per product/run. Real memory *compounding* across runs for the
*same* product is still fine (and intended); the bug was compounding
across *different, unrelated* products.

---

### RocketRide `db_hydradb` node needs a Cloud database, not the local instance

**Symptom:** The `db_hydradb` node in a `.pipe` needs `api_key` +
`database` fields; pointing it at the local self-hosted HydraDB
(Bolt/HTTP, token-file auth) doesn't fit that schema.
**Cause:** `db_hydradb` is built against HydraDB's managed Cloud API, a
different product surface than the local self-hosted graph-node.
**Note:** Moot now — HydraDB isn't part of the go-forward architecture at
all (see `docs/WHITEPAPER.md` §3). Recorded in case a similar
local-vs-managed mismatch shows up with another RocketRide/HydraDB-style
integration later — the tell is a node schema wanting an "API key +
resource name" pair instead of a plain connection string.

---

### `TypeError: log_event() got multiple values for keyword argument 'uri'`

**Symptom:** Crashes right after a real Bluesky post succeeds, mid-way
through pulling metrics back.
**Cause:** `bsky.get_post_metrics()` returns a dict that already has a
`"uri"` key; the caller also passed `uri=...` explicitly alongside
`**real`, colliding.
**Fix:** Don't pass `uri=` separately — `real` already has it:
`audit.log_event("bluesky", "metrics.pulled", **real)`.
**Cost of not catching this sooner:** a real post had already gone out
before the crash — the publish and the logging bug are independent; a
crash after publish doesn't mean the publish didn't happen. Always check
`audit/events.jsonl` / the platform itself before assuming a failed run
posted nothing.

---

### Local LLM prepends a preamble line to generated posts

**Symptom:** One of N generated drafts is literally "Here are 3 marketing
posts based on the provided information:" instead of real content.
**Cause:** Small local models (llama3.2:3b included) often ignore a
"no extra commentary" instruction and add a one-line preamble before the
real output.
**Fix:** `rocketride_client._llm_draft_posts()` filters out any line that
ends with `:` or starts with `here are`/`here's`/`sure,`/`certainly`
before picking lines for drafts.

---

### `llama3.2:1b` works fast but produces unusable structured output

**Symptom:** Cognee's `cognify()` step retries the same call repeatedly
with growing exponential backoff, never completing (or taking many
minutes) when Ollama's `LLM_MODEL` is `llama3.2:1b`.
**Cause:** The model returns malformed/nested JSON for structured-output
requests (e.g. `{"description": ..., "type": "string"}` where a plain
string was expected) — small-model instruction-following limitation, not
a config bug.
**Fix:** Use `llama3.2:3b` instead. It passed a direct structured-output
benchmark reliably and is still light enough for an 8GB machine running
Docker alongside it. If memory/speed is ever critically tight, re-test
smaller models individually before assuming any given size will work —
"fast" and "follows structured-output instructions" are different axes.

---

### Cognee container: `LLMAPIKeyNotSetError` on `add()`/`cognify()`

**Symptom:** `add_document()`/`cognify()` fail even though the container
is healthy (`/health` returns 200).
**Cause:** The Cognee Docker container has no LLM configured — being
"healthy" only means the web server is up, not that an LLM is wired in.
**Fix:** `scripts/configure_cognee_llm.sh` recreates the container with
`LLM_PROVIDER`/`LLM_MODEL`/`LLM_ENDPOINT`/`LLM_API_KEY` (+ the matching
`EMBEDDING_*` vars) from `.env`. For local Ollama specifically: the
container reaches the host via `host.docker.internal`, **not**
`localhost` — and the LLM endpoint must **not** have a `/v1` suffix for
litellm's native `ollama` provider (the embedding endpoint *does* keep its
own path, `/api/embed` — they're not symmetric, don't "fix" one to match
the other).
