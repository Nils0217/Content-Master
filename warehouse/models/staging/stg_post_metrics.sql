-- Raw source: metrics/post_metrics.jsonl — every metrics reading recorded
-- by pipeline.py's step6_track_metrics() (real platform pulls and
-- simulated readings alike, distinguishable via `source`). Replaces
-- hotdata.dev's role (see ../../src/contentmaster/metrics_store.py,
-- docs/SCHEDULE.md Phase 0) — a full history, not just the latest
-- reading per post (unlike plays/*.json's last_metrics, which only keeps
-- the most recent one).
select
    post_id,
    channel,
    recorded_at::timestamp as recorded_at,
    source,
    impressions,
    clicks,
    conversions,
    ctr
from read_json_auto('../metrics/post_metrics.jsonl', format = 'newline_delimited', union_by_name = true)
