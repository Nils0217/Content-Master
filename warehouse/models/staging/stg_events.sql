-- Raw source: audit/events.jsonl — every pipeline event (see
-- ../src/automarketer/audit.py). Useful for latency/funnel analysis
-- (e.g. time from cognee/add.start to publish, drop-off at human review).
select
    ts::timestamp as ts,
    stage,
    event
from read_json_auto('../audit/events.jsonl', format = 'newline_delimited', union_by_name = true)
