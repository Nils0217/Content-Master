-- Raw source: warehouse/seeds/webSearchcat_region_clean.csv (Google Trends
-- "cat" interest by country, one snapshot — no date column). Not row-
-- joinable to performance_history (posts aren't tagged by target region),
-- so this stays a standalone reference table: query it directly for
-- "where is this topic hottest right now" context, e.g. from analysis.py.
select
    "Country"               as country,
    websearch_region_cat::double as relative_interest
from {{ ref('webSearchcat_region_clean') }}
