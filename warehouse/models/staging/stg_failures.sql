-- Raw source: plays/_failures.jsonl — rejected drafts and posts that
-- didn't clear the success threshold (see modiqo_play.capture_failure).
-- These are just as valuable as successes: they're the negative examples
-- future improvement/RL work needs.
select
    product,
    channel,
    text,
    reason,
    improvement_note
from read_json_auto('../plays/_failures.jsonl', format = 'newline_delimited', union_by_name = true)
