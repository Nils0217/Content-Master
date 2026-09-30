---
name: parse-what-models-return
description: Use whenever asking a model for a structured answer, writing or changing a parser for model output, or debugging "it returned nothing". This project has lost data five times to a model answering correctly in a format the parser did not accept — and every time the loss was silent, because an empty parse looks exactly like nothing to report.
---

# A model's format is a request, not a contract

You ask for a shape. The model answers in its own. If the parser only
accepts the shape you asked for, a correct answer is discarded — and a
discarded answer and a genuinely empty one are the same empty list.

## What has actually happened here

| asked for | came back | cost |
|---|---|---|
| `POST: …` at line start | `-- POST: …` on every line | 0 drafts from a complete, usable reply |
| blocks split by `---` | `-- ` written *before* each block | 0 beats; the video script looked like a model failure |
| `Topic: X \| Brief: Y` | `1. **Spraying in Cats**: This section…` | the whole expanded whitepaper — every new topic unreachable, while the index recorded the document as processed |
| a five-word noun phrase | 1964 characters summarising the document | stored as the category, then pasted into every drafting and image prompt, where the drafting model picked a topic out of *the summary* instead of the index |
| `axis = side` | `axis = tone, side: negative` | test axis recorded as the literal word "axis" |

Five times, one week, three different models. It is not a model being
unusual — it is the normal case.

## Rules

**Parse by content, not by punctuation.** The field name is the signal.
Bullets, numbering, bold markers, quote carets and stray dashes are
decoration models add to lists, and any of them at the start of a line
must not change the meaning.

**Accept the shape models actually produce, not only the one you asked
for.** Try the requested format first so a well-formed answer is never
reinterpreted, then fall back to the numbered/bulleted list. A long prose
answer to a short question is a different problem — reject that, because
storing it crowds out the real inputs.

**Separators are hints.** `parse_script` flushes a beat when a second
`SAY:` arrives, so the separator is optional. A parser that depends on
punctuation the model was merely *asked* to produce breaks on the next
model.

**Empty must never mean two things.** "Parsed nothing" and "there was
nothing" have to be distinguishable, and the caller has to be told which
one happened. This is where all five incidents did their damage: the run
carried on, the source was marked processed, and nothing said a word.
Where a hash or a "done" marker records that input was handled, do not
write it when the parse produced nothing from a non-empty answer.

## Before trusting a parser

1. **Print what the model actually returned**, in full, from the real
   call. Not what the prompt asked for — what came back.
2. Feed that verbatim string to the parser as a test case. Every parser
   in `tests/test_parsers.py` has one, taken from a real failure.
3. Try the decorated variants: `* X:`, `1. X:`, `> X:`, `-- X:`, `**X**:`.
4. Check the empty path: if the parser returns nothing, does the caller
   say so, and does anything get marked as done?

Step 1 is the one that matters. Every incident above was diagnosed in
seconds once the raw reply was on screen, and none was visible from the
prompt, the code, or the audit log.
