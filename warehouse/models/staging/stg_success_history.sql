-- Raw source: plays/_history.jsonl — an append-only ledger of every
-- successful capture over time (see ../src/contentmaster/modiqo_play.py's
-- _append_history()). Unlike plays/*.json (overwritten each run,
-- current-state only), this is what lets analysis.py compare a
-- product/channel's performance across runs, not just look at the latest
-- one — the whole point of "cross validate with historical data".
select
    ts::timestamp as ts,
    product,
    channel,
    winning_text as post_text,
    metrics.impressions as impressions,
    metrics.clicks as clicks,
    metrics.conversions as conversions,
    metrics.ctr as ctr,
    reviewer_note,
    improvement_note,
    run_number
from read_json_auto('../plays/_history.jsonl', format = 'newline_delimited', union_by_name = true)
