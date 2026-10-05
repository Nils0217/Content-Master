-- Raw source: plays/_reasoning.jsonl — every model call behind every
-- analysis (see ../src/contentmaster/reasoning.py). One row per step:
-- each advisor's prompt and raw reply, then the judge's. Added 2026-10-04,
-- when finding where a hypothesis went missing meant matching audit-log
-- lines by timestamp, because the advisor proposals carried no post_id
-- and the prompts were not stored at all.
--
-- `outcome` says what happened to the analysis as a whole, so an empty
-- hypothesis is never ambiguous: picked / only_one_usable / judge_unusable
-- / none_usable / no_response (hypothesis mode), synthesized / unmerged /
-- single_model / no_response (evidence mode).
--
-- The file is created by the first `contentmaster review` after this
-- change; until then this model fails with "file not found", which is
-- the truth — there is nothing to read yet.
--
-- Typical use: every step behind one post's 7d analysis —
--   select step_no, step, role, model, raw from stg_reasoning
--   where post_id = 'draft-…' and checkpoint = '7d' order by ts, step_no;
with records as (
    select *
    from read_json(
        '../plays/_reasoning.jsonl',
        format = 'newline_delimited',
        columns = {
            reasoning_id: 'VARCHAR',
            ts: 'VARCHAR',
            post_id: 'VARCHAR',
            checkpoint: 'VARCHAR',
            product: 'VARCHAR',
            channel: 'VARCHAR',
            mode: 'VARCHAR',
            outcome: 'VARCHAR',
            result_text: 'VARCHAR',
            hypothesis: 'STRUCT(id VARCHAR, test VARCHAR, why VARCHAR, watch VARCHAR, watch_raw VARCHAR, model VARCHAR, picked VARCHAR, pick_reason VARCHAR)',
            steps: 'STRUCT(step VARCHAR, role VARCHAR, model VARCHAR, prompt VARCHAR, raw VARCHAR)[]'
        }
    )
)
select
    reasoning_id,
    ts::timestamp as ts,
    post_id,
    checkpoint,
    product,
    channel,
    mode,
    outcome,
    hypothesis.id as hypothesis_id,
    hypothesis.test as hypothesis_test,
    hypothesis.watch as hypothesis_watch,
    hypothesis.picked as picked_advisor,
    hypothesis.pick_reason as pick_reason,
    result_text,
    s.step_no,
    s.step.step as step,
    s.step.role as role,
    s.step.model as model,
    s.step.prompt as prompt,
    s.step.raw as raw
from records,
    unnest(list_transform(steps, (x, i) -> {'step_no': i, 'step': x})) as t(s)
