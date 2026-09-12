-- Raw source: plays/*.json — one file per (product, channel) with the
-- current winning post, its real pulled metrics, and the LLM's improvement
-- note (see ../src/automarketer/modiqo_play.py). DuckDB reads JSON natively,
-- no Python ETL step needed.
select
    product,
    channel,
    winning_text,
    last_metrics.impressions   as impressions,
    last_metrics.clicks        as clicks,
    last_metrics.conversions   as conversions,
    last_metrics.ctr           as ctr,
    reviewer_note,
    improvement_note,
    runs
from read_json_auto('../plays/*.json', union_by_name = true)
