#!/usr/bin/env python3
"""The real Phase 5 review page (2026-09-17), reading from the actual
draft queue now instead of generating on the spot the way the first MVP
did (see docs/LOG.md for that earlier prototype and the design session
behind this version).

`contentmaster run` (default, no --terminal) writes here after
generating each draft and its image. This page shows what is pending,
lets you approve, edit, send feedback to the LLM, or reject, and on
approve calls the same publish path the terminal flow already uses.

2026-09-18 (Phase 5 extended to `contentmaster review`, /grill-me design
session, see docs/LOG.md): a second tab, "Analysis review", does the same
thing for `analysis.confirm_with_human()`'s old blocking input() —
`contentmaster review` (default, no --terminal) queues each due post's
metrics + analysis + recommendation here instead, and this page lets you
Confirm or Disagree (with a note) in the browser. One shared page/process
for both tabs on purpose (a /grill-me decision): `pipeline.launch_streamlit()`
is the single launcher either command calls, so `run` and `review` never
race to open two servers on the same port.

Run with:
    ./.venv/bin/streamlit run streamlit_app.py
(normally launched for you by `contentmaster run` / `contentmaster review`.)
"""
import os
import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import streamlit as st

from contentmaster import analysis_queue, draft_queue
from contentmaster.draft_generator import DraftGenerator
from contentmaster.modiqo_play import capture_failure
from contentmaster.pipeline import apply_analysis_decision, step5_publish, step6_queue_for_review
from contentmaster.pipeline import PublishFailed
from contentmaster.publish_guard import publish_blockers
from contentmaster.platforms.registry import get_platform

# 2026-09-19 (real usage feedback, see docs/LOG.md): auto-shut this server
# down once nobody's had a tab open for this long, rather than leaving it
# to eat memory in the background forever on a machine that's already been
# OOM-killed once this project (see docs/LOG.md's earlier Streamlit
# incidents). Deliberately an idle timeout, not a browser
# beforeunload/unload hook: that event fires on a plain page refresh too
# (which this project's own review workflow does often, e.g. to pick up a
# code change), so tying shutdown to it would kill the server on a refresh.
_IDLE_SHUTDOWN_SECONDS = 300
_IDLE_CHECK_INTERVAL_SECONDS = 20
_IDLE_WATCHDOG_THREAD_NAME = "contentmaster-idle-watchdog"


def _watch_idle_shutdown() -> None:
    """Runs for the lifetime of the process on its own daemon thread.
    Uses Streamlit's internal (non-public) Runtime/_session_mgr API — a
    deliberate tradeoff, agreed with the user, for not needing a separate
    supervisor process. If a future Streamlit upgrade renames or removes
    this, the `except Exception` below just makes this thread a no-op
    (never shuts down on uncertainty) rather than crashing the app.
    """
    idle_since: float | None = None
    while True:
        time.sleep(_IDLE_CHECK_INTERVAL_SECONDS)
        try:
            from streamlit.runtime import get_instance

            n_active = get_instance()._session_mgr.num_active_sessions()
        except Exception:  # noqa: BLE001 — never shut down when we can't tell
            continue
        if n_active > 0:
            idle_since = None
            continue
        if idle_since is None:
            idle_since = time.monotonic()
        elif time.monotonic() - idle_since >= _IDLE_SHUTDOWN_SECONDS:
            os.kill(os.getpid(), signal.SIGTERM)
            return


def _ensure_idle_watchdog_started() -> None:
    """Streamlit re-executes this whole script on every rerun (every
    widget interaction, for every connected session), so a plain
    module-level guard isn't reliable — checking real OS thread state via
    `threading.enumerate()` is, regardless of how Streamlit's script
    runner handles module-level state across reruns.

    Real constraint, verified 2026-09-19: Streamlit doesn't execute this
    script at all until the *first* browser session actually connects, so
    this watchdog only starts once someone has genuinely opened the page
    at least once — a server whose auto-open silently failed and that
    nobody ever visited won't self-clean. Acceptable: that's a narrower,
    already-loud failure (see launch_streamlit()'s own printed fallback
    URL) than the "opened once, then forgotten" case this is built for.
    """
    if any(t.name == _IDLE_WATCHDOG_THREAD_NAME for t in threading.enumerate()):
        return
    threading.Thread(target=_watch_idle_shutdown, name=_IDLE_WATCHDOG_THREAD_NAME, daemon=True).start()


_ensure_idle_watchdog_started()

st.set_page_config(page_title="ContentMaster review", page_icon="📝")
st.title("ContentMaster review")


def _nav_index(idx_key: str, n: int) -> int:
    """2026-09-19 (real usage feedback): both tabs used to always show
    `pending[0]` — no way to look back and forth across several pending
    items before deciding on any one of them. Previous/Next just move an
    index into the (already ts-sorted) pending list; per-item state
    (text edits, expanded feedback/disagree boxes) stays keyed by that
    item's own id, so paging back and forth never loses anything.
    """
    idx = max(0, min(st.session_state.get(idx_key, 0), n - 1))
    nav_prev, nav_label, nav_next = st.columns([1, 3, 1])
    if nav_prev.button("< Previous", key=f"{idx_key}_prev", disabled=(idx == 0)):
        idx -= 1
    if nav_next.button("Next >", key=f"{idx_key}_next", disabled=(idx == n - 1)):
        idx += 1
    nav_label.caption(f"{idx + 1} of {n} pending")
    st.session_state[idx_key] = idx
    return idx


def _show_labels(entry: dict) -> None:
    """What the model said this draft is, and what it is testing.

    2026-09-22: these fields have been generated and stored since the
    drafting redesign, and this page showed none of them — so the one
    instruction that actually matters at review time ("check what it put
    in EVIDENCE-AGAINST") could not be followed here at all, and the
    browser is the default review surface.

    EVIDENCE-AGAINST gets its own callout because a control post is
    supposed to do worse. Approving one without realising it is a control
    means reading its result as a verdict on the content rather than as a
    test of the current pattern.
    """
    axis, arm = entry.get("test_axis"), entry.get("test_arm")
    against = (entry.get("evidence_against") or "").strip()
    is_control = bool(against) and against.lower() not in ("none", "n/a", "-", "nothing")

    if is_control:
        st.warning(f"**Control post — deliberately goes against the evidence.** {against}\n\n"
                   "It is expected to underperform. That is the point: if it does not, the "
                   "pattern we have been following has stopped being true.")
    with st.expander("What the model says this draft is", expanded=False):
        if entry.get("topic"):
            st.write(f"**Topic:** {entry['topic']}")
        if entry.get("topic_reason"):
            st.write(f"**Why this topic:** {entry['topic_reason']}")
        if entry.get("characteristics"):
            st.write("**Characteristics:** " + ", ".join(entry["characteristics"]))
        if axis:
            st.write(f"**Testing:** {axis} = {arm or '(no side given)'}")
        else:
            st.caption("No test axis — this draft is not comparing anything.")
        if entry.get("evidence_used"):
            st.write(f"**Evidence followed:** {entry['evidence_used']}")
        if not is_control:
            st.caption("Evidence deliberately contradicted: none.")


def _show_length(channel: str, text: str) -> None:
    """Live character count against the target platform's own cap.

    2026-09-19 (code scan): nothing checked length anywhere before
    publish. The generation prompt asks for "under 280 characters" and
    that is all — a longer draft (or a longer human edit) reached
    bluesky.publish_post(), which raised, and pipeline.step5_publish()
    did not catch it, taking the run down. That raise is a proper
    platform error now, but the reviewer should see the problem here,
    before approving, not as a warning afterwards.
    """
    try:
        cap = get_platform(channel).max_post_chars
    except Exception:  # noqa: BLE001 — unimplemented/unconfigured platform: no cap to show
        cap = None
    n = len(text)
    if cap is None:
        st.caption(f"{n} characters")
    elif n > cap:
        st.error(f"{n} characters — over {channel}'s {cap} limit. Edit it down before approving.")
    else:
        st.caption(f"{n} / {cap} characters")


tab_drafts, tab_analysis = st.tabs(["Draft review", "Analysis review"])

with tab_drafts:
    pending = draft_queue.find_pending()

    if not pending:
        st.info("Nothing waiting for review right now. Run `contentmaster run` to queue a draft.")
    else:
        idx = _nav_index("draft_nav_idx", len(pending))
        entry = pending[idx]
        draft_id = entry["draft_id"]

        text_key = f"text_{draft_id}"
        if text_key not in st.session_state:
            st.session_state[text_key] = entry["text"]

        st.subheader(f"{entry['product']} on {entry['channel']}, topic: {entry.get('topic') or 'none'}")

        # 2026-09-19 (code scan): a draft produced by the fixed template
        # (no topic index AND no usable context — see
        # draft_generator.draft_posts) used to look exactly like a
        # generated one here, which is how a run of identical canned posts
        # went unnoticed for days. Say so plainly instead.
        source = entry.get("source", "unknown")
        if source == "template":
            st.error("This draft is the canned fallback template, not generated content — "
                     "Cognee and/or Ollama were unreachable. Rejecting it and re-running "
                     "once they are up is usually what you want.")
        elif source == "context":
            st.warning("No topic index for this product — generated from the whitepaper text "
                       "read locally. Start Cognee for topic-scoped drafts.")

        st.write(st.session_state[text_key])
        _show_length(entry["channel"], st.session_state[text_key])
        _show_labels(entry)

        if entry.get("image_path") and Path(entry["image_path"]).exists():
            st.image(entry["image_path"], caption="Generated image")
        else:
            st.caption("No image for this draft (Cloudflare not configured, or the call failed).")

        col_a, col_e, col_f, col_r = st.columns(4)

        if col_a.button("Approve and publish"):
            draft = {"id": draft_id, "channel": entry["channel"], "topic": entry.get("topic", ""),
                      "brief": entry.get("brief", "")}
            # 2026-09-19 (real usage bug, see docs/LOG.md): this used to call
            # step5_publish() with no image argument at all, so an already
            # human-approved, already-generated image (shown right above this
            # button) silently never made it into the real post — text-only
            # every time, even though the terminal path's step4b_generate_image
            # always attaches one when present.
            image_bytes = None
            if entry.get("image_path") and Path(entry["image_path"]).exists():
                image_bytes = Path(entry["image_path"]).read_bytes()
            image_alt = (entry.get("brief") or st.session_state[text_key])[:200]
            try:
                published = step5_publish(
                    draft, st.session_state[text_key], image=image_bytes, image_alt=image_alt,
                    # The text as first generated, kept by the Edit box below
                    # — publish_guard compares against it to tell a terse
                    # rewrite from feedback typed into the wrong box.
                    original_text=entry.get("original_text") or entry["text"],
                    acknowledged=bool(st.session_state.get(f"ack_short_{draft_id}")),
                )
            except PublishFailed as e:
                # Nothing was sent, and the page must not say otherwise —
                # showing `Published.` after a failed publish is exactly
                # how draft-2d62b063 went unnoticed for two days.
                st.error(f"Not published — {e.failure.explain()}")
                draft_queue.update_entry(
                    draft_id, status="publish_failed",
                    failure_kind=e.failure.kind, failure_reason=e.failure.reason,
                )
                if e.failure.retry_unchanged:
                    st.info("The post itself is fine. Leave it queued and approve it again once "
                            "the platform is reachable.")
                elif e.failure.needs_revision:
                    st.info("Edit the text or use **Send feedback** to have it revised, then "
                            "approve again.")
                    # Only an unusual-looking post can be waived; a platform's
                    # own rule cannot, so this is offered but may not help.
                    st.checkbox("I have read the above and this really is the post text I want",
                                key=f"ack_short_{draft_id}")
                st.stop()
            step6_queue_for_review(
                entry["product"], entry["channel"], published,
                # 2026-09-19 (code scan): was `st.session_state[text_key]
                # != entry["text"]`. Now that an LLM revision is written
                # back to the queue entry (so it survives a refresh), both
                # sides match after a revision and that comparison always
                # said "not edited" — exactly backwards. The queue entry
                # carries an explicit `edited` flag instead.
                edited=bool(entry.get("edited")) or st.session_state[text_key] != entry["text"],
                reviewer_note=entry.get("reviewer_note", ""),
            )
            draft_queue.update_entry(draft_id, status="published", text=st.session_state[text_key])
            st.success(f"Published. {published.get('status')}")
            st.rerun()

        if col_e.button("Edit"):
            st.session_state[f"show_edit_{draft_id}"] = True
        if st.session_state.get(f"show_edit_{draft_id}"):
            # 2026-09-20: this box used to be labeled just "Replacement
            # text", with no confirmation — the same ambiguity that once
            # published a reviewer's note ("add some details") as a live
            # post. The terminal prompt was fixed for this on 2026-09-15;
            # this UI, added 2026-09-18, did not inherit it. Wording now
            # matches, and publish_guard.py backs both up regardless.
            st.caption("The FULL text of the post, exactly as it should appear. "
                       "To describe a change instead and have the LLM rewrite it, "
                       "use **Send feedback**.")
            new_text = st.text_area("Replacement post text", value=st.session_state[text_key],
                                     key=f"edit_box_{draft_id}")
            for problem in publish_blockers(new_text, entry.get("original_text") or entry["text"]):
                st.warning(problem)
            if st.button("Save edit", key=f"save_edit_{draft_id}"):
                # 2026-09-19 (code scan): the edit used to live only in
                # st.session_state, so refreshing the page (which this
                # project's own workflow does routinely) silently threw it
                # away and showed the original text again. Written back to
                # the queue now, same as an LLM revision.
                st.session_state[text_key] = new_text
                draft_queue.update_entry(
                    draft_id, text=new_text, edited=True,
                    # Written once, on the first edit: the text as generated,
                    # so publish_guard still has something to compare against
                    # after `text` has been overwritten.
                    original_text=entry.get("original_text") or entry["text"],
                )
                st.session_state[f"show_edit_{draft_id}"] = False
                st.rerun()

        if col_f.button("Send feedback"):
            st.session_state[f"show_feedback_{draft_id}"] = True
        if st.session_state.get(f"show_feedback_{draft_id}"):
            feedback = st.text_input("What should change?", key=f"feedback_box_{draft_id}")
            if st.button("Send to the LLM", key=f"send_feedback_{draft_id}"):
                with st.spinner("Revising..."):
                    revised = DraftGenerator().revise_post(
                        entry["product"], st.session_state[text_key], feedback,
                        brief=entry.get("brief", ""), channel=entry["channel"],
                    )
                if revised:
                    st.session_state[text_key] = revised
                    # 2026-09-19 (code scan): the feedback a reviewer typed
                    # here used to exist only in this browser session — the
                    # queue entry's `reviewer_note` stayed "" forever, so
                    # the note handed to Modiqo on publish was always empty
                    # and the real human signal ("make it shorter", "drop
                    # the hashtag") never reached the next run's prompt.
                    # The terminal path has always recorded this; the
                    # browser path silently dropped it.
                    notes = [n for n in (entry.get("reviewer_note", ""), feedback) if n]
                    draft_queue.update_entry(
                        draft_id, reviewer_note="; ".join(notes), text=revised, edited=True,
                    )
                    st.session_state[f"show_feedback_{draft_id}"] = False
                    st.rerun()
                else:
                    st.error("LLM revision failed. Try again.")

        if col_r.button("Reject"):
            st.session_state[f"show_reject_{draft_id}"] = True
        if st.session_state.get(f"show_reject_{draft_id}"):
            # 2026-09-19 (code scan): the old handler read
            # st.session_state[f"reject_reason_{draft_id}"] — a key nothing
            # in this file ever wrote, so the reason was permanently the
            # constant "rejected in review UI". Every rejection in
            # plays/_failures.jsonl says exactly that and nothing more,
            # while the terminal path has always asked "Why rejected?".
            # Those rejections are the negative examples the improvement
            # loop is supposed to learn from; without a reason they teach
            # nothing.
            reject_reason = st.text_input(
                "Why rejected? (this is the learning signal — what was wrong with it?)",
                key=f"reject_reason_{draft_id}",
            )
            if st.button("Confirm reject", key=f"confirm_reject_{draft_id}"):
                notes = [n for n in (entry.get("reviewer_note", ""), reject_reason.strip()) if n]
                reason = "; ".join(notes) or "rejected in review UI (no reason given)"
                capture_failure(entry["product"], entry["channel"], st.session_state[text_key], reason)
                draft_queue.update_entry(draft_id, status="rejected", reviewer_note=reason)
                st.session_state[f"show_reject_{draft_id}"] = False
                st.info("Rejected.")
                st.rerun()

with tab_analysis:
    pending_analysis = analysis_queue.find_pending()

    if not pending_analysis:
        st.info("Nothing waiting for an analysis decision right now. Run `contentmaster review` "
                 "to queue one once a post's checkpoint is due.")
    else:
        idx = _nav_index("analysis_nav_idx", len(pending_analysis))
        a_entry = pending_analysis[idx]
        post_id, checkpoint = a_entry["post_id"], a_entry["checkpoint"]
        a_key = f"{post_id}:{checkpoint}"
        analysis = a_entry["analysis"]
        metrics = a_entry["metrics"]
        recommendation = a_entry["recommendation"]
        tracking_entry = a_entry["entry"]

        st.subheader(f"{tracking_entry['product']} on {tracking_entry['channel']} — {checkpoint} checkpoint")
        st.write(tracking_entry.get("final_text", ""))

        st.write(f"prior published posts at this tier: {analysis.get('n_prior_posts')}")
        if analysis.get("historical_avg_score") is not None:
            st.write(f"historical avg score: {analysis['historical_avg_score']:.4f}")
        st.write(f"current score: {metrics.get('engagement_score', 0):.4f}")
        st.write(f"trend: {analysis.get('trend')}")
        if analysis.get("prior_suggestion"):
            st.write(f"prior suggestion: {analysis['prior_suggestion']!r} — looks "
                     f"{analysis.get('prior_suggestion_effectiveness')}")
        st.caption(analysis.get("evidence", ""))
        if analysis.get("external_signal"):
            st.caption(analysis["external_signal"])
        if analysis.get("trending_context"):
            st.caption(analysis["trending_context"])

        st.markdown(f"**Recommendation for the next post:** {recommendation}")

        col_c, col_d = st.columns(2)

        if col_c.button("Confirm", key=f"confirm_{a_key}"):
            apply_analysis_decision(a_entry, confirmed=True, note="")
            st.success("Confirmed.")
            st.rerun()

        if col_d.button("Disagree", key=f"disagree_{a_key}"):
            st.session_state[f"show_disagree_{a_key}"] = True
        if st.session_state.get(f"show_disagree_{a_key}"):
            note = st.text_area(
                "What should the next post do instead? (kept *alongside* the recommendation "
                "above, not replacing it — both get read next time)",
                key=f"disagree_note_{a_key}",
            )
            if st.button("Submit disagreement", key=f"submit_disagree_{a_key}"):
                apply_analysis_decision(a_entry, confirmed=False, note=note)
                st.session_state[f"show_disagree_{a_key}"] = False
                st.info("Disagreement recorded.")
                st.rerun()
