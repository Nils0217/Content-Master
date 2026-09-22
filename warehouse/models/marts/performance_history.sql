-- The table analysis.py actually queries. Unlike campaign_performance
-- (current-state snapshot — at most one "published" row per
-- product/channel), this is a real timeline: every successful post and
-- every rejection, in order, so trend and "did the last suggestion help"
-- questions have real data to answer instead of guessing from N=1.
--
-- 2026-09-14: first real "cross validate against other data" join — see
-- warehouse/models/staging/stg_google_trends_timeline.sql (the only one of
-- the 4 new Google Trends seeds with a per-day grain; the other 3 —
-- related-queries top/rising, region — aren't row-joinable and stay
-- standalone reference tables for the draft/discuss prompt instead).
with history as (
    select
        ts,
        product,
        channel,
        post_text,
        'published' as status,
        outcome as engagement_outcome,  -- 2026-09-18: "success"/"failure" at this checkpoint, distinct from `status` (which is about publishing, not engagement)
        like_count,
        repost_count,
        reply_count,
        bookmark_count,
        quote_count,
        engagement_score,
        reviewer_note,
        improvement_note,
        run_number,
        checkpoint
    from {{ ref('stg_success_history') }}

    union all

    select
        ts,
        product,
        channel,
        text as post_text,
        'rejected' as status,
        null as engagement_outcome,  -- rejected at human review, never published, no engagement to score
        null as like_count,
        null as repost_count,
        null as reply_count,
        null as bookmark_count,
        null as quote_count,
        null as engagement_score,
        reason as reviewer_note,
        improvement_note,
        null as run_number,
        checkpoint
    from {{ ref('stg_failures') }}
    -- 2026-09-19 (code scan): `where checkpoint is null` is the whole
    -- point of this filter. plays/_failures.jsonl holds TWO different
    -- kinds of row (see modiqo_play.capture_failure):
    --   checkpoint IS NULL     — a draft a human rejected at review. It
    --                            never published. 'rejected' is correct.
    --   checkpoint IS NOT NULL — a post that DID publish and then came in
    --                            under SUCCESS_CTR_THRESHOLD at that
    --                            checkpoint. Since 2026-09-18 this also
    --                            appends to plays/_history.jsonl (tagged
    --                            outcome='failure'), so it already
    --                            arrives above via stg_success_history —
    --                            with its real metrics attached.
    -- Without this filter the second kind appeared TWICE: once correctly
    -- as 'published' with metrics, and once again as 'rejected' with
    -- every metric null. Verified in warehouse/local.duckdb: 'Wiring Test
    -- Product' had exactly that pair. Calling a post that really went out
    -- 'rejected' is also just wrong, independent of the duplication.
    where checkpoint is null
)

select
    history.*,
    trend.cat_interest as external_cat_search_interest
from history
left join {{ ref('stg_google_trends_timeline') }} as trend
    on trend.day = history.ts::date

-- 2026-09-19 (code scan): drops rows with no timestamp, which failed this
-- model's own not_null_performance_history_ts test (3 rows, pre-existing).
-- They are the oldest entries in plays/_failures.jsonl, written before
-- capture_failure() recorded `ts` at all. This model is explicitly a
-- timeline — analysis.py orders by ts and joins Google Trends on it — so
-- a row with no point in time cannot be placed on it, cannot be trended,
-- and cannot be cross-validated. They remain in stg_failures and in
-- campaign_performance (a current-state model with no time axis), so
-- nothing is lost, it just stops pretending they belong in a series.
where history.ts is not null

order by ts
