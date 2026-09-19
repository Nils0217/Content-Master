-- Raw source: warehouse/seeds/google_trends_cat_related_queries_top.csv
-- (Google Trends "related queries" — TOP panel, 0-100 relative score, no
-- date). Reference/context table, not row-joinable to performance_history
-- — meant to be pulled into the draft/discuss prompt as "what people are
-- actually searching for around this topic right now".
select
    query,
    score::int as score
from {{ ref('google_trends_cat_related_queries_top') }}
