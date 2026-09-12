-- The one table most future work should read from: every post attempt
-- (published or rejected), with real metrics where they exist. This is
-- the training/analysis set the eventual RL fine-tuning or MCP memory
-- plugin will draw from.
select
    product,
    channel,
    winning_text as post_text,
    'published'  as status,
    impressions,
    clicks,
    conversions,
    ctr,
    reviewer_note,
    improvement_note,
    runs
from {{ ref('stg_plays') }}

union all

select
    product,
    channel,
    text as post_text,
    'rejected'   as status,
    null as impressions,
    null as clicks,
    null as conversions,
    null as ctr,
    reason as reviewer_note,
    improvement_note,
    null as runs
from {{ ref('stg_failures') }}
