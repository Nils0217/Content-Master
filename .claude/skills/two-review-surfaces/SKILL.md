---
name: two-review-surfaces
description: Use when changing anything a human sees or approves in this project — draft review, image review, analysis review, publish guards, error messages, or any print/render of a verdict. This repo has two review surfaces (terminal and Streamlit) with separate code, and a fix applied to one has silently missed the other five times.
---

# Two review surfaces, one of them is the default

Every human touchpoint in this project exists twice:

| what the human does | terminal | browser (**the default**) |
|---|---|---|
| review a draft | `human_loop.review_draft()` | `streamlit_app.py` draft tab |
| review an image/video | `human_loop.review_image()`, called from `pipeline.step4b_generate_image()` | `pipeline._run_streamlit()` + `st.image`/`st.video` |
| review an analysis | `analysis._print_analysis()` | `streamlit_app.py` analysis tab |
| run the loop | `pipeline._run_terminal()` | `pipeline._run_streamlit()` |

`contentmaster run` and `contentmaster review` default to the **browser**.
`--terminal` is the opt-in. So a fix that lands only in the terminal path
lands in the path almost nobody uses.

## What keeps happening

Five times, in one week:

1. The `[e]dit` prompt published a reviewer's feedback as a live post.
   Fixed in the terminal prompt. Streamlit, written three days later, had
   the same ambiguity and no confirmation step.
2. The image prompt was rebuilt from the whitepaper sentence. Fixed in
   `step4b_generate_image`; `_run_streamlit` still built it the old way —
   and that is the path that produced the garbled-text image.
3. An approved image was never attached to the real post, because the
   Streamlit approve button called `step5_publish` without it.
4. `launch_streamlit()` only ran when the current invocation queued
   something, so a run where everything was already pending told the
   operator to open a URL nothing had started.
5. `_print_analysis` got a truncation and a fixed label; the Streamlit
   analysis tab still dumps the full note and still prints `looks unknown`.

Every one was found by a human looking at the browser, not by a test.

## The rule

**Put the behaviour below both surfaces.** A check, a guard, a
classification or a formatting decision belongs in a module both paths
call, not in either path's own rendering code. The one fix in this list
that did not recur is `publish_guard.py`, because it sits in
`step5_publish()` — the single chokepoint both surfaces already go
through.

When that genuinely is not possible — `st.warning` has no terminal
equivalent — then **change both in the same edit**, and say in the commit
that you did.

## Before saying a UI change works

`import contentmaster.pipeline` succeeding proves nothing. It passed while
`from contentmaster.pipeline import StaleAnalysis` was failing, because
importing a module does not check that a name exists in it.

1. `./.venv/bin/python -m py_compile streamlit_app.py`
2. `./.venv/bin/python -c "from contentmaster.pipeline import <every name streamlit imports>"`
3. **Restart Streamlit.** A running server holds the old modules in
   memory; editing a file it already imported changes nothing until it
   restarts. This is what the `StaleAnalysis` ImportError actually was.
4. **Open the page and look at it.** Screenshot the tab you changed.

Step 4 is the one that has caught every instance above. Steps 1–3 caught
none of them.

## Claiming confidence

Do not report a UI change as done on the strength of a passing import.
Say which of the four steps above were actually run. "Compiles" and
"works in the browser" are different claims, and this project has a
week of evidence that they come apart.
