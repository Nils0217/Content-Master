# warehouse — dbt + DuckDB (+ MotherDuck)

Replaces hotdata.dev's role (the hackathon-mandated analytics layer) with a
fully local, zero-account stack: DuckDB does the SQL, dbt does the
cleaning/transformation, MotherDuck is the optional cloud target once this
needs to be shared/queried by more than one machine.

## Setup

```bash
cd "Marketing hack"
./.venv/bin/pip install dbt-duckdb   # already in requirements.txt
export DBT_PROFILES_DIR=warehouse
cd warehouse
dbt run     # builds against local.duckdb (gitignored)
dbt test    # (add tests as the project grows)
```

Query the result directly:
```bash
./.venv/bin/python -c "
import duckdb
con = duckdb.connect('warehouse/local.duckdb')
print(con.sql('select * from campaign_performance').df())
"
```
or open it in DuckDB's own CLI (`brew install duckdb`) for an interactive
prompt — no server, no login, no dashboard to fight with.

## Switching to MotherDuck (shared/cloud)

1. Free account at https://motherduck.com, create a token (Settings ->
   Tokens).
2. `export MOTHERDUCK_TOKEN=...` (put it in `.env`, never commit it).
3. `dbt run --target cloud` — same models, same SQL, now materialized in
   MotherDuck instead of the local file. First run auto-creates the
   `contentmaster` database in your MotherDuck account.

## Layout

```
models/
  staging/     one model per raw source (plays/*.json, _failures.jsonl,
               audit/events.jsonl) — thin, 1:1 with the source, no logic
  marts/       campaign_performance.sql — the one table most analysis
               should read from (published + rejected posts, unioned,
               with real metrics where they exist)
seeds/         drop reference CSVs here (news/current-events data, etc.)
profiles.yml   connection config — no secrets in it (MotherDuck token is
               read from an env var), safe to commit
local.duckdb   the actual database file — gitignored, regenerate anytime
               with `dbt run`
```

## Why this over hotdata.dev

No CLI to auth, no workspace/database to provision, no account at all for
the local path. Same SQL either way (DuckDB and hotdata's DataFusion engine
are both embedded, both real SQL) — this just removes every piece of
account/UX friction hotdata added, and gives a clear one-command upgrade
path (MotherDuck) for when local-only stops being enough.
