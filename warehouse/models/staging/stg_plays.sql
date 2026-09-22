-- Raw source: plays/*.json — one file per (product, channel) with the
-- current winning post, its real pulled metrics, and the LLM's improvement
-- note (see ../src/contentmaster/modiqo_play.py). DuckDB reads JSON natively,
-- no Python ETL step needed.
--
-- 2026-09-21: the glob is now checked before it is read. DuckDB's
-- read_json_auto raises `IO Error: No files found that match the pattern`
-- on an empty match rather than returning zero rows, which took the whole
-- dbt build down the moment plays/ had no play file in it. That is not an
-- edge case: a play file is only written when a post is judged `proven`
-- (scoring.py's gates, replacing the old absolute threshold), so a fresh
-- account has none at all until the first real win — which is the normal
-- state for weeks, not an error.
{%- set has_plays = false -%}
{%- if execute -%}
    {%- set result = run_query("select count(*) as n from glob('../plays/*.json')") -%}
    {%- set has_plays = result.columns[0].values()[0] > 0 -%}
{%- endif -%}

{% if has_plays %}
select
    product,
    channel,
    winning_text,
    last_metrics.like_count       as like_count,
    last_metrics.repost_count     as repost_count,
    last_metrics.reply_count      as reply_count,
    last_metrics.bookmark_count   as bookmark_count,
    last_metrics.quote_count      as quote_count,
    last_metrics.engagement_score as engagement_score,
    reviewer_note,
    improvement_note,
    runs
from read_json_auto('../plays/*.json', union_by_name = true)
{% else %}
-- No play files yet. Typed empty result so every downstream model keeps
-- its columns and types instead of failing to compile.
select
    cast(null as varchar) as product,
    cast(null as varchar) as channel,
    cast(null as varchar) as winning_text,
    cast(null as bigint)  as like_count,
    cast(null as bigint)  as repost_count,
    cast(null as bigint)  as reply_count,
    cast(null as bigint)  as bookmark_count,
    cast(null as bigint)  as quote_count,
    cast(null as double)  as engagement_score,
    cast(null as varchar) as reviewer_note,
    cast(null as varchar) as improvement_note,
    cast(null as bigint)  as runs
where false
{% endif %}
