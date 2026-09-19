-- Raw source: warehouse/seeds/google_trends_cat_related_queries_rising.csv
-- (Google Trends "related queries" — RISING panel: query, is_breakout,
-- growth_pct, no date). Same role as stg_google_trends_related_top:
-- reference/context table for the draft/discuss prompt, not a
-- performance_history join.
select
    query,
    is_breakout::boolean as is_breakout,
    growth_pct::int      as growth_pct
from {{ ref('google_trends_cat_related_queries_rising') }}
