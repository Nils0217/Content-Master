-- Raw source: plays/_posts.jsonl (../src/contentmaster/posts.py).
-- Grain: ONE ROW PER POST — the latest state of each post_id. The ledger
-- itself is append-only (one line per state change); this keeps the last.
--
-- natural_id is the human-facing sequence (per product × channel, given at
-- approval); post_id is the system key; cluster_id ties the post to its
-- week-long hypothesis cluster (0 = before clusters existed); kind is
-- 'cluster' or 'extra'; experiment marks the ~20% of a cluster that
-- deliberately differs from its hypothesis (null for extras and cluster 0).
-- Added 2026-10-06 — before this no table had one row per post.
with lines as (
    select *, row_number() over (partition by post_id order by seq desc) as rn
    from read_json(
        '../plays/_posts.jsonl',
        format = 'newline_delimited',
        columns = {
            post_id: 'VARCHAR', product: 'VARCHAR', channel: 'VARCHAR',
            natural_id: 'BIGINT', cluster_id: 'BIGINT', kind: 'VARCHAR',
            experiment: 'BOOLEAN', status: 'VARCHAR', scheduled_for: 'VARCHAR',
            scheduled_at: 'VARCHAR', published_at: 'VARCHAR', post_ref: 'VARCHAR',
            ts: 'VARCHAR', seq: 'BIGINT'
        }
    )
)
select
    natural_id, post_id, product, channel, cluster_id, kind, experiment, status,
    scheduled_for::date as scheduled_for,
    scheduled_at::timestamp as scheduled_at,
    published_at::timestamp as published_at,
    post_ref,
    ts::timestamp as updated_at
from lines
where rn = 1
