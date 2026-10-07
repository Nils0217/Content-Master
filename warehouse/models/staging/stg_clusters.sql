-- Raw source: plays/_clusters.jsonl (../src/contentmaster/clusters.py).
-- Grain: ONE ROW PER CLUSTER (product × channel × cluster_id), latest state.
--
-- A cluster is one calendar week (Mon–Sun), one hypothesis, seven posts:
-- five follow the hypothesis, two (experiment_weekdays) deliberately
-- differ. `month` is the month of the cluster's Monday — a cluster is never
-- split across months; analyse by cluster, not by calendar month.
-- learned_from = the cluster whose results chose this hypothesis (null when
-- planned without one).
with lines as (
    select *, row_number() over (partition by product, channel, cluster_id order by seq desc) as rn
    from read_json(
        '../plays/_clusters.jsonl',
        format = 'newline_delimited',
        columns = {
            product: 'VARCHAR', channel: 'VARCHAR', cluster_id: 'BIGINT',
            week_start: 'VARCHAR', experiment_weekdays: 'VARCHAR[]',
            hypothesis: 'STRUCT(id VARCHAR, test VARCHAR, why VARCHAR, watch VARCHAR)',
            hypothesis_id: 'VARCHAR', learned_from: 'BIGINT', post_time: 'VARCHAR',
            status: 'VARCHAR', ts: 'VARCHAR', seq: 'BIGINT'
        }
    )
)
select
    product, channel, cluster_id,
    week_start::date as week_start,
    strftime(week_start::date, '%Y-%m') as month,
    experiment_weekdays,
    hypothesis_id,
    hypothesis.test as hypothesis_test,
    hypothesis.watch as hypothesis_watch,
    learned_from, post_time, status,
    ts::timestamp as updated_at
from lines
where rn = 1
