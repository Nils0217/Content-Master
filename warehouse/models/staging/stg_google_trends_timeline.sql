-- Raw source: warehouse/seeds/webSearch_cat_timeline.csv (Google Trends
-- "cat" interest-over-time, daily). This is the only one of the 4 new
-- seeds with a per-day grain, so it's the one that can actually be
-- LEFT JOINed onto performance_history by date — see
-- ../marts/performance_history.sql. Phase 1 "seed one real external CSV" /
-- Phase 9 "cross-validate against other data" (docs/SCHEDULE.md).
select
    "Day"::date                    as day,
    websearch_world_trend_cat::int as cat_interest
from {{ ref('webSearch_cat_timeline') }}
