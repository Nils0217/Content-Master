-- The table analysis.py actually queries. Unlike campaign_performance
-- (current-state snapshot — at most one "published" row per
-- product/channel), this is a real timeline: every successful post and
-- every rejection, in order, so trend and "did the last suggestion help"
-- questions have real data to answer instead of guessing from N=1.
--
-- Also where any external "other data" (news/current-events CSVs, etc. —
-- see warehouse/seeds/) should get joined in once there's something to
-- join against — that's the "cross validate against other data" half of
-- the analysis step this table exists for.
select
    ts,
    product,
    channel,
    post_text,
    'published' as status,
    impressions,
    clicks,
    conversions,
    ctr,
    reviewer_note,
    improvement_note,
    run_number
from {{ ref('stg_success_history') }}

union all

select
    ts,
    product,
    channel,
    text as post_text,
    'rejected' as status,
    null as impressions,
    null as clicks,
    null as conversions,
    null as ctr,
    reason as reviewer_note,
    improvement_note,
    null as run_number
from {{ ref('stg_failures') }}

order by ts
