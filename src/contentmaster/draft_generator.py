"""Draft generation — calls the local Ollama LLM directly to write posts
grounded in Cognee's extraction, optionally improving on a prior run's
result (see modiqo_play.find_best_prior — pipeline.py's actual source for
`prior` since 2026-09-15's checkpoint redesign; find_play() still exists
but only for the plain "current snapshot" case, not this fallback).

Renamed from rocketride_client.py 2026-09-13 (see docs/LOG.md): that name
was misleading — draft generation here has never depended on RocketRide's
cloud service, only ever called Ollama. This is the permanent architecture
decision (docs/WHITEPAPER.md §3 "Text generation": local, free, private,
swappable), not a temporary stand-in for something else.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any

import requests

_LLM_ENDPOINT = os.environ.get("LLM_IMPROVE_ENDPOINT", "http://localhost:11434/v1/chat/completions")
_LLM_MODEL = os.environ.get("LLM_IMPROVE_MODEL", "llama3.2:3b")


def _target_instruction(target: dict[str, Any] | None) -> str:
    """2026-09-18 (real design review): a target is opt-in and currently
    only a region/audience string (see topic_index.load_target) — set via
    `contentmaster run --target-region`/`--target-audience`, testing phase
    only has region in real use. Returns "" when nothing's set, so a
    product with no target writes exactly like before this existed.
    """
    if not target:
        return ""
    region, audience = target.get("region"), target.get("audience")
    if not region and not audience:
        return ""
    parts = [p for p in (f"region: {region}" if region else "", f"audience: {audience}" if audience else "") if p]
    return (
        f"\nThis post's target is {', '.join(parts)}. Write with wording, references, or "
        "hashtags that would genuinely resonate there where it fits naturally, but don't "
        "force an unrelated region or audience reference in if it doesn't fit.\n"
    )


# "Post 1:", "Draft 2 -", "Topic 3.", "For Topic 1:" — an enumerator
# prefix the model added despite being told not to. Unambiguous enough to
# strip blindly; a real marketing post never opens this way. The optional
# leading "for " is not hypothetical: llama3.2:3b produced
# "For Topic 1: Benefits - ..." on a real run while this was being fixed.
_ENUMERATOR_PREFIX = re.compile(
    r"^(?:for\s+)?(?:topic|post|draft)\s*\d*\s*[:.\-\u2013]\s*", re.IGNORECASE
)


def _clean_llm_lines(content: str, topic_names: list[str] | None = None) -> list[str]:
    """Small local models often ignore "no extra commentary" and add a
    one-line preamble ("Here are N posts:") before the real content —
    drop lines that look like a header/preamble rather than a post.
    Shared by both _llm_draft_posts() and _llm_draft_posts_for_topics().

    `topic_names` (2026-09-19, code scan): the prompt says "no topic
    labels", and small models ignore that too — a real published post
    went out reading "Lifestyle: Cats thrive on independence...", where
    "Lifestyle" was this draft's own topic name leaking into the copy.
    Only a prefix matching one of *these* names is stripped, so a post
    that legitimately opens "Warning: ..." is left alone.
    """
    lines = [line.strip("-* \t") for line in content.splitlines() if line.strip()]
    lines = [
        line for line in lines
        if not (line.endswith(":") or line.lower().startswith(("here are", "here's", "sure,", "certainly")))
    ]
    return [_strip_label(line, topic_names or []) for line in lines]


def _strip_label(line: str, topic_names: list[str]) -> str:
    """Remove a leading enumerator ("Post 2:") or a leading copy of this
    draft's own topic name ("Lifestyle:") — see _clean_llm_lines().
    """
    # Loop: the two prefixes stack. A real run produced
    # "For Topic 1: Benefits - <the actual post>", which needs the
    # enumerator stripped before the topic name is even at the front.
    for _ in range(3):
        before = line
        line = _ENUMERATOR_PREFIX.sub("", line, count=1).strip()
        for name in topic_names:
            name = name.strip()
            if not name:
                continue
            prefix = re.compile(rf"^{re.escape(name)}\s*[:.\-\u2013]\s*", re.IGNORECASE)
            line = prefix.sub("", line, count=1).strip()
        if line == before:
            break
    return line


class DraftGenerator:
    def draft_posts(
        self,
        product: dict[str, Any],
        channel: str,
        n: int = 1,
        context: str = "",
        prior: dict[str, Any] | None = None,
        topics: list[dict[str, Any]] | None = None,
        target: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Turns Cognee's extracted product knowledge into N draft posts
        for one channel.

        `topics` (2026-09-15, see topic_index.py) is the preferred path —
        one {"topic", "brief"} per draft, picked by pipeline.py as the
        product's currently least-used topics. Each draft is grounded in
        only *its own* topic's brief, and carries a `topic` field back so
        pipeline.py can record it as used once the post actually
        publishes. This is what gives real variety across runs without a
        hardcoded "draft 1 covers X" template — the topics themselves come
        from the document, not from code.

        `context` is the fallback for when no topic index exists yet
        (Cognee down, or nothing extracted) — same LLM call, just not
        topic-scoped. 2026-09-19 (code scan): this was dead for months.
        pipeline.step3_generate_drafts() never passed it, so `context` was
        always "" and the branch below was unreachable; a Cognee outage
        skipped the LLM entirely and every draft came out as the fixed
        template string below. pipeline.py now builds it from the
        whitepaper read locally (see document.read_text) plus the
        product's own name/features, so the model still writes something
        real when Cognee is down.

        Every returned draft carries a `source` field — "topics",
        "context" or "template" — so the audit log and the review UI can
        tell a generated post from the canned one. Without it, a
        template-only run logged exactly like a healthy run, which is why
        this went unnoticed for so long.

        `prior` (a Modiqo play record — see modiqo_play.find_best_prior) is
        the highest-maturity checkpoint's winning post + its real metrics +
        the LLM's own improvement note for this product/channel. When
        present, this run doesn't just replay the old text: it writes a
        genuinely new version that acts on that specific feedback — the
        "another loop" from the white paper.

        `target` (2026-09-18, see topic_index.load_target): an optional
        {"region", "audience"} the product is being written for. None/empty
        means write generically, same as before this existed.
        """
        name = product.get("name", "the product")
        if topics:
            drafts = self._llm_draft_posts_for_topics(name, channel, topics, prior, target)
            if drafts:
                return drafts
        if context.strip():
            drafts = self._llm_draft_posts(name, channel, n, context, prior, target)
            if drafts:
                return drafts

        # Last resort. Reaching here means BOTH the topic index and the
        # context fallback failed, i.e. there is no grounding material at
        # all and/or the local LLM is unreachable — not a normal run.
        print("[warn] No topics and no usable context, or the local LLM did not respond — "
              "falling back to the fixed template. These drafts are NOT generated; check "
              "that Cognee and Ollama are up, and that --whitepaper points at a real file.")
        features = product.get("features", [])
        drafts = []
        for i in range(n):
            feature = features[i % len(features)] if features else "what it does"
            drafts.append(
                {
                    "id": f"draft-{uuid.uuid4().hex[:8]}",
                    "channel": channel,
                    "text": f"{name} — {feature}. Built for teams who ship fast. #{channel}",
                    "status": "draft",
                    "source": "template",
                }
            )
        return drafts

    def _llm_draft_posts_for_topics(
        self, name: str, channel: str, topics: list[dict[str, Any]], prior: dict[str, Any] | None = None,
        target: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None:
        """One draft per topic, each grounded only in that topic's brief —
        real variety from distinct extracted facts, not a shuffled reword
        of one shared context blob. Returns None on any failure so the
        caller can fall back — never raises.
        """
        n = len(topics)
        topic_lines = "\n".join(
            f"Topic {i}: {t['topic']} — {t['brief']}" for i, t in enumerate(topics, start=1)
        )
        prompt = (
            f"Here is a product called '{name}'. Write one short marketing post for {channel} "
            f"(under 280 characters) for EACH of the following {n} distinct topics extracted "
            f"from its source document. Ground each post only in the fact given for its own "
            f"topic — do not invent claims, and do not mix facts from other topics into it.\n\n"
            f"{topic_lines}\n\n"
        )
        if prior:
            m = prior.get("last_metrics", {})
            prompt += (
                f"A previous post for this product/channel was: \"{prior.get('winning_text', '')}\"\n"
                f"Its real results: {m.get('clicks', 0)} likes, {m.get('conversions', 0)} reposts, "
                f"ctr={m.get('ctr', 0)}.\n"
                f"An LLM review of that result suggested this specific improvement: "
                f"\"{prior.get('improvement_note', '')}\"\n"
            )
            # 2026-09-16: a human reviewer's own feedback (from
            # analysis.confirm_with_human()), stored *alongside* the LLM's
            # improvement_note above, not in place of it — both are shown;
            # where they conflict, the human's direction wins.
            if prior.get("human_feedback"):
                prompt += (
                    f"A human reviewer additionally said: \"{prior['human_feedback']}\"\n"
                    "Where this differs from the LLM's suggestion above, prioritize what the "
                    "human said.\n"
                )
            prompt += "Apply that improvement across all the posts below where it makes sense.\n\n"
        prompt += _target_instruction(target)
        prompt += (
            f"Reply with exactly {n} lines, one post per line, IN THE SAME ORDER as the topics "
            "above (line 1 = Topic 1, etc.). No numbering, no topic labels, no extra commentary."
        )
        try:
            resp = requests.post(
                _LLM_ENDPOINT,
                json={"model": _LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.8},
                timeout=90,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
        except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError):
            return None

        lines = _clean_llm_lines(content, [t["topic"] for t in topics])
        if not lines:
            return None

        # 2026-09-19 (code scan): used to be `lines[i % len(lines)]` while
        # still labelling draft i with topics[i] — so whenever the model
        # returned a different number of lines than topics (routine for a
        # 3B model), a draft got text about one topic tagged with a
        # different topic's name. step6_queue_for_review() then called
        # record_topic_used() on that wrong name, permanently skewing
        # which topics pick_topics() considers "already used". Pair
        # strictly by position now and return fewer drafts rather than
        # mislabel any.
        paired = min(len(lines), n)
        if paired < n:
            print(f"[warn] Asked the model for {n} post(s), got {paired} usable line(s) — "
                  f"writing {paired} draft(s) rather than reusing text under the wrong topic.")
        drafts = []
        for i in range(paired):
            drafts.append({
                "id": f"draft-{uuid.uuid4().hex[:8]}", "channel": channel, "text": lines[i],
                "status": "draft", "topic": topics[i]["topic"], "brief": topics[i]["brief"],
                "source": "topics",
            })
        return drafts or None

    def revise_post(self, name: str, current_text: str, feedback: str, brief: str = "") -> str | None:
        """2026-09-15 — the "big fix" for human_loop's edit flow: a human
        typing feedback like "add some details" used to become the
        *literal* post text (a real broken post went out this way — see
        docs/LOG.md). Now that feedback goes through here instead: one
        LLM call, asked to rewrite `current_text` applying `feedback`,
        still grounded in the same topic's brief. Returns None on any
        failure so the caller can fall back (e.g. offer a literal edit
        instead) — never raises.
        """
        prompt = (
            f"Here is a draft marketing post for '{name}': \"{current_text}\"\n"
        )
        if brief:
            prompt += f"It's grounded in this fact: {brief}\n"
        prompt += (
            f"A reviewer asked for this specific change: \"{feedback}\"\n"
            "Rewrite the post applying that change. Keep it under 280 characters, still "
            "grounded in the same fact above — do not invent new claims. Reply with ONLY "
            "the revised post text, no commentary, no quotes around it."
        )
        try:
            resp = requests.post(
                _LLM_ENDPOINT,
                json={"model": _LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.8},
                timeout=90,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
        except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError):
            return None
        lines = _clean_llm_lines(content)
        return lines[0] if lines else None

    def _llm_draft_posts(
        self, name: str, channel: str, n: int, context: str, prior: dict[str, Any] | None = None,
        target: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None:
        """Ask the local LLM for N posts grounded in Cognee's extracted
        content. Returns None on any failure so the caller can fall back —
        never raises.
        """
        prompt = (
            f"Here is what we know about a product called '{name}', extracted from its "
            f"source document:\n\n{context.strip()[:3000]}\n\n"
        )
        if prior:
            m = prior.get("last_metrics", {})
            prompt += (
                f"A previous post for this product/channel was: \"{prior.get('winning_text', '')}\"\n"
                f"Its real results: {m.get('clicks', 0)} likes, {m.get('conversions', 0)} reposts, "
                f"ctr={m.get('ctr', 0)}.\n"
                f"An LLM review of that result suggested this specific improvement: "
                f"\"{prior.get('improvement_note', '')}\"\n"
            )
            # 2026-09-16: see the matching comment in _llm_draft_posts_for_topics
            if prior.get("human_feedback"):
                prompt += (
                    f"A human reviewer additionally said: \"{prior['human_feedback']}\"\n"
                    "Where this differs from the LLM's suggestion above, prioritize what the "
                    "human said.\n"
                )
            prompt += _target_instruction(target)
            prompt += (
                f"\nWrite {n} distinct, NEW short marketing posts for {channel} (under 280 characters "
                "each) that actually apply that improvement and are not just a reword of the previous "
                "post. Still ground every claim in the source text above — do not invent claims not "
                "supported by it. Reply with exactly one post per line, no numbering, no extra commentary."
            )
        else:
            prompt += _target_instruction(target)
            prompt += (
                f"Write {n} distinct, short marketing posts for {channel} (under 280 characters "
                "each) that reference concrete facts from the text above. Do not invent claims "
                "not supported by the text. Reply with exactly one post per line, no numbering, "
                "no extra commentary."
            )
        try:
            resp = requests.post(
                _LLM_ENDPOINT,
                json={"model": _LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.8},
                timeout=90,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
        except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError):
            return None

        lines = _clean_llm_lines(content)
        if not lines:
            return None
        # Same positional pairing as _llm_draft_posts_for_topics() — there
        # are no topic labels to mismatch here, but cycling still produced
        # duplicate drafts presented as distinct ones.
        paired = min(len(lines), n)
        if paired < n:
            print(f"[warn] Asked the model for {n} post(s), got {paired} usable line(s) — "
                  f"writing {paired} draft(s) rather than repeating one.")
        drafts = [
            {"id": f"draft-{uuid.uuid4().hex[:8]}", "channel": channel, "text": lines[i],
             "status": "draft", "source": "context"}
            for i in range(paired)
        ]
        return drafts or None
