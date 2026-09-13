-- Raw source: plays/_failures.jsonl — rejected drafts and posts that
-- didn't clear the success threshold (see modiqo_play.capture_failure).
-- These are just as valuable as successes: they're the negative examples
-- future improvement/RL work needs.
--
-- Explicit `columns` (not read_json_auto's inferred schema) because `ts`
-- was added to capture_failure() after this file already had rows — with
-- pure inference, a column present in zero *current* rows doesn't exist
-- in the inferred schema at all, not just read as null. Forcing the
-- schema means `ts` is always there (null for pre-existing rows) instead
-- of the whole model erroring until a fresh failure is captured.
select
    try_cast(ts as timestamp) as ts,
    product,
    channel,
    text,
    reason,
    improvement_note
from read_json(
    '../plays/_failures.jsonl',
    format = 'newline_delimited',
    columns = {
        ts: 'VARCHAR',
        product: 'VARCHAR',
        channel: 'VARCHAR',
        text: 'VARCHAR',
        reason: 'VARCHAR',
        improvement_note: 'VARCHAR'
    }
)
