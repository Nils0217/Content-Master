-- Raw source: plays/_history.jsonl — an append-only ledger of every
-- checkpointed, published post over time (see
-- ../src/contentmaster/modiqo_play.py's _append_history()). Unlike
-- plays/*.json (overwritten each run, current-state only), this is what
-- lets analysis.py compare a product/channel's performance across runs,
-- not just look at the latest one — the whole point of "cross validate
-- with historical data".
-- 2026-09-18: despite the model name (kept as-is for now to avoid a wider
-- rename), this is no longer success-only — capture_failure() started
-- appending here too (tagged `outcome` = "failure") once a real analysis
-- session found the old success-only version threw away every failed
-- checkpoint's improvement_note and kept reporting "no_history" for a
-- product that had actually published several posts already. Rows from
-- before this changed have no `outcome` field and read as null here.
-- `checkpoint` (2026-09-15, docs/SCHEDULE.md Phase 9): which tier
-- (24h/7d/30d) this row's analysis was run at — rows from before this
-- field existed read as null here. Explicit `columns` (not
-- read_json_auto's inferred schema), same reasoning stg_failures.sql
-- already documents for `ts`: a column present in zero *current* rows
-- doesn't exist in the inferred schema at all, not just read as null, so
-- inference alone would make it vanish rather than read null.
-- analysis.py filters on `checkpoint` so a 24h reading is never compared
-- against a 7d/30d one.
select
    ts::timestamp as ts,
    product,
    channel,
    winning_text as post_text,
    metrics.like_count as like_count,
    metrics.repost_count as repost_count,
    metrics.reply_count as reply_count,
    metrics.bookmark_count as bookmark_count,
    metrics.quote_count as quote_count,
    metrics.engagement_score as engagement_score,
    reviewer_note,
    improvement_note,
    run_number,
    checkpoint,
    outcome
from read_json(
    '../plays/_history.jsonl',
    format = 'newline_delimited',
    columns = {
        ts: 'VARCHAR',
        product: 'VARCHAR',
        channel: 'VARCHAR',
        winning_text: 'VARCHAR',
        metrics: 'STRUCT(like_count BIGINT, repost_count BIGINT, reply_count BIGINT, bookmark_count BIGINT, quote_count BIGINT, engagement_score DOUBLE)',
        reviewer_note: 'VARCHAR',
        improvement_note: 'VARCHAR',
        run_number: 'BIGINT',
        checkpoint: 'VARCHAR',
        outcome: 'VARCHAR'
    }
)
