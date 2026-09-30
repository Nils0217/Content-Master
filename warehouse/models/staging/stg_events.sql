-- Raw source: audit/events.jsonl — every pipeline event (see
-- ../src/contentmaster/audit.py). Useful for latency/funnel analysis
-- (e.g. time from cognee/add.start to publish, drop-off at human review).
--
-- 2026-09-24: an explicit `columns` schema, not read_json_auto. The log is
-- deliberately heterogeneous — every call to audit.log_event passes
-- whatever fields that event has — and it had grown to 62 distinct key
-- names across 458 rows. Past that, DuckDB's auto-detection gives up and
-- returns the whole file as one `json` column, at which point
-- `ts::timestamp as ts` fails with "Column ts referenced that exists in
-- the SELECT clause but cannot be referenced before it is defined", which
-- names the symptom and not the cause.
--
-- Nothing had changed here; a day of new event types was enough to cross
-- the threshold. That is the point: with a log that gains fields as the
-- code does, inference is a dependency on data shape and will break again
-- without warning. stg_failures.sql and stg_success_history.sql already
-- learned this (see docs/ERROR_LOG.md — `ts`, then `checkpoint`); this is
-- the third time and the last place still relying on inference.
--
-- Listing only the three fields this model actually uses also means a new
-- event type can never affect it again.
select
    ts::timestamp as ts,
    stage,
    event
from read_json(
    '../audit/events.jsonl',
    format = 'newline_delimited',
    union_by_name = true,
    columns = {ts: 'VARCHAR', stage: 'VARCHAR', event: 'VARCHAR'}
)
