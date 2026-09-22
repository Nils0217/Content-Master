-- The one table most future work should read from: every post attempt
-- (published or rejected), with real metrics where they exist. This is
-- the training/analysis set the eventual RL fine-tuning or MCP memory
-- plugin will draw from.
select
    product,
    channel,
    winning_text as post_text,
    'published'  as status,
    like_count,
    repost_count,
    reply_count,
    bookmark_count,
    quote_count,
    engagement_score,
    reviewer_note,
    improvement_note,
    runs
from {{ ref('stg_plays') }}

union all

-- 2026-09-19 (code scan): `status` used to be the literal 'rejected' for
-- every row of stg_failures. But that file mixes never-published drafts
-- (checkpoint is null) with posts that DID publish and then underperformed
-- at a checkpoint (checkpoint is not null) — see the longer note in
-- performance_history.sql. Labelling the second kind 'rejected' misstates
-- what happened to a real post that is live on the platform right now.
-- Unlike performance_history, these rows are NOT dropped here: this model
-- unions against stg_plays (a current-state snapshot of winners only), so
-- for an underperforming post this is the only record of it.
select
    product,
    channel,
    text as post_text,
    case when checkpoint is null then 'rejected' else 'published' end as status,
    null as like_count,
    null as repost_count,
    null as reply_count,
    null as bookmark_count,
    null as quote_count,
    null as engagement_score,
    reason as reviewer_note,
    improvement_note,
    null as runs
from {{ ref('stg_failures') }}
