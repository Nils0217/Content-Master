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

from .config import settings

from .scoring import score_of as _score_of

_LLM_ENDPOINT = os.environ.get("LLM_IMPROVE_ENDPOINT", "http://localhost:11434/v1/chat/completions")
_LLM_MODEL = os.environ.get("LLM_IMPROVE_MODEL", "llama3.2:3b")


def _limit_instruction(channel: str) -> str:
    """The channel's own hard rules, read from its adapter.

    2026-09-21: replaces "under 280 characters", which was hardcoded as a
    string in four separate prompts here while the adapter declared 300.
    Neither number was wrong on its own — having two was. The LLM was told
    280, produced 322, and nothing checked until the platform rejected it
    (draft-2d62b063).
    """
    try:
        from .platforms.registry import get_platform

        described = get_platform(channel).constraints.describe_for_prompt()
    except Exception:
        # An unimplemented channel has no constraints to state. Saying
        # nothing is correct; inventing a number is how this started.
        return ""
    return f" Hard platform rules: {described}." if described else ""


def _enforce_constraints(lines: list[str], channel: str) -> list[str]:
    """Drop generated lines that break the channel's hard rules.

    2026-09-21: a draft that violates a published platform limit must
    never reach the review queue. It cannot be published, so offering it
    for human approval wastes the reviewer's attention and — before
    step5_publish started refusing — ended with the platform rejecting it
    after the human had already approved it (draft-2d62b063, 322 chars).

    Dropping rather than truncating: cutting 22 characters off the end
    removes whatever the writer put last, which on these posts is the
    hashtags or the closing line. Returning fewer drafts is honest;
    returning a mangled one is not.
    """
    try:
        from .platforms.registry import get_platform

        constraints = get_platform(channel).constraints
    except Exception:
        return lines
    kept = []
    for line in lines:
        problems = constraints.violations(line)
        if problems:
            print(f"[dropped] A generated draft broke {channel}'s rules and was not queued: "
                  f"{'; '.join(problems)}.")
            continue
        kept.append(line)
    return kept


# --- structured draft blocks -------------------------------------------
#
# 2026-09-21. The model no longer returns one post per line. It has to say
# which topic it chose and why, what each draft IS, what it is TESTING,
# and which evidence it deliberately went against — none of which fits on
# one line.
#
# A block format rather than JSON on purpose: docs/ERROR_LOG.md already
# records `llama3.2:1b` producing unusable structured output, and a 3B
# model that drops a closing brace destroys a whole JSON document, while a
# malformed block costs one draft. Every field except POST is optional at
# parse time for the same reason — a draft that arrives without a test
# axis is still a usable draft, and validate_group() will say so.
_BLOCK_SEPARATOR = "---"
_FIELD_PATTERN = re.compile(r"^(POST|TOPIC|WHY|IS|TESTING|EVIDENCE-USED|EVIDENCE-AGAINST)\s*:\s*(.*)$",
                            re.IGNORECASE)


def _parse_blocks(content: str) -> list[dict[str, Any]]:
    blocks, current, last_key = [], {}, None
    for raw in content.splitlines():
        line = raw.strip()
        if line.startswith(_BLOCK_SEPARATOR) and set(line) <= {"-"}:
            if current.get("POST"):
                blocks.append(current)
            current, last_key = {}, None
            continue
        match = _FIELD_PATTERN.match(line)
        if match:
            last_key = match.group(1).upper()
            current[last_key] = match.group(2).strip()
        elif line and last_key:
            # A wrapped continuation line. Only POST is worth joining; a
            # model rambling after TESTING is noise, not more label.
            if last_key == "POST":
                current["POST"] = (current["POST"] + " " + line).strip()
    if current.get("POST"):
        blocks.append(current)
    return blocks


def _split_labels(value: str) -> list[str]:
    return [p.strip().lower() for p in re.split(r"[,;]", value or "") if p.strip()]


def _parse_axis(value: str) -> tuple[str, str]:
    """"opening style = question" -> ("opening style", "question")."""
    if not value:
        return "", ""
    if "=" in value:
        axis, arm = value.split("=", 1)
    elif ":" in value:
        axis, arm = value.split(":", 1)
    else:
        return value.strip().lower(), ""
    return axis.strip().lower(), arm.strip().lower()


def _match_topic(name: str, topics: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Map the model's TOPIC back to a real index entry.

    Exact match first, then case-insensitive, then a prefix match — a 3B
    model writes "Risks" for "Risks" but also "risks " and occasionally
    "Risks (outdoor)". Anything it cannot resolve returns None and the
    draft is dropped: a draft filed under a topic that does not exist would
    corrupt the usage counts that topic selection now reads (this is the
    same failure recorded at draft_generator.py's strict-pairing comment).
    """
    if not name:
        return None
    wanted = name.strip().lower()
    for t in topics:
        if t["topic"].strip().lower() == wanted:
            return t
    for t in topics:
        actual = t["topic"].strip().lower()
        if wanted.startswith(actual) or actual.startswith(wanted):
            return t
    return None

def _trending_for_prompt() -> str:
    """Current search interest, stated with the date it was captured.

    2026-09-21. Until now trends reached drafting only second-hand: the
    analysis step wrote them into an `improvement_note` as prose, and that
    whole note was pasted into the next generation prompt. So the model
    saw stale data quoted inside an even staler opinion, with no date
    attached, and could not tell how old any of it was.

    The data is still hand-exported seed CSV (warehouse/seeds/), currently
    ending 2026-09-13 — automating the refresh is scheduled, not done. The
    date is therefore not decoration: "cat litter" as a steady top search
    is fine a week later, while a breakout trend a week later is
    archaeology, and only the model can judge which matters for the post
    it is writing. Saying when the data is from is what makes that
    judgement possible.

    Region is deliberately excluded. Where the audience is already a human
    decision (--target-region, see topic_index.load_target); adding "these
    countries search for this most" would quietly compete with it.
    """
    db_path = settings.project_root / "warehouse" / "local.duckdb"
    if not db_path.exists():
        return ""
    try:
        import duckdb

        con = duckdb.connect(str(db_path), read_only=True)
        try:
            top = [r[0] for r in con.execute(
                "select query from stg_google_trends_related_top order by score desc limit 8"
            ).fetchall()]
            rising = con.execute(
                "select query, is_breakout, growth_pct from stg_google_trends_related_rising "
                "order by is_breakout desc, growth_pct desc nulls first limit 5"
            ).fetchall()
            as_of = con.execute(
                "select max(day) from stg_google_trends_timeline"
            ).fetchone()
        finally:
            con.close()
    except Exception:  # noqa: BLE001 — optional context, never worth failing a run over
        return ""

    if not top and not rising:
        return ""
    captured = str(as_of[0])[:10] if as_of and as_of[0] else "an unknown date"
    parts = [f"SEARCH INTEREST, captured {captured} (judge for yourself whether it is still "
             "current — this data is not live):"]
    if top:
        parts.append("- Steady top related searches: " + ", ".join(top) + ".")
    if rising:
        described = ", ".join(
            f"{q} ({'breakout' if b else f'+{int(g)}%' if g else 'rising'})" for q, b, g in rising
        )
        parts.append(f"- Rising searches as of {captured}: {described}. These move fast and go "
                     "stale faster than the steady ones.")
    parts.append("You may use any of this as a hook, or ignore it. Nothing here is a fact about "
                 "the product, so it cannot be used as a claim.")
    return "\n".join(parts)

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
            drafts = self._llm_draft_posts_for_topics(name, channel, topics, prior, target, n=n)
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
        target: dict[str, Any] | None = None, n: int | None = None,
    ) -> list[dict[str, Any]] | None:
        """Drafts grounded in the whitepaper's extracted topics.

        2026-09-21, /grill-me redesign. What changed and why:

        * The model now CHOOSES its topics instead of being handed one per
          draft. Topic selection used to happen in pick_topics() before the
          model saw anything, and was then stated as an absolute rule
          ("ground each post only in the fact given for its own topic"),
          while the improvement note was appended at the end hedged with
          "where it makes sense". Those two instructions contradicted each
          other and the absolute one always won — so the advice could never
          change what a post was about, only how it was worded. Twelve
          posts about indoor/outdoor cat risks is what that produces.
        * Usage counts are shown rather than enforced. The model cannot see
          its own history, which is the actual reason it repeats itself;
          being told is enough, and a hard "not the same topic twice" rule
          would just be pick_topics() again under another name.
        * Prior results are given as EVIDENCE, with no verb attached. The
          old line "Apply that improvement across all the posts below" is
          gone. Phase 10 settled that a proven pattern is evidence and not
          an instruction; this is that decision reaching the prompt.
        * A share of every group MUST contradict the evidence — see
          scoring.control_arm_size(). Wording alone cannot stop an LLM
          treating a statistic as an order, so the control arm is required
          rather than encouraged.
        * Each draft declares what it IS and what it is TESTING, reusing
          the existing vocabulary where it fits (draft_labels.py).

        Returns None on any failure so the caller can fall back — never
        raises.
        """
        from . import draft_labels

        wanted = n or len(topics)
        # Counted across a whole test group, not this batch — a batch of 1
        # can never be 20% of anything (draft_labels.controls_needed).
        controls = draft_labels.controls_needed(name, channel, wanted)
        topic_lines = "\n".join(
            f"- {t['topic']} (used {t.get('used_count', 0)} time(s)): {t['brief']}"
            for t in topics
        )

        prompt = (
            f"You are writing {wanted} short marketing post(s) for {channel} about a product "
            f"called '{name}'.\n\n"
            f"AVAILABLE TOPICS, extracted from the product's source document. Choose which to "
            f"write about — you may use the same topic more than once or leave some unused. "
            f"The usage counts are there so you can see what has already been covered a lot; "
            f"they are information, not a rule.\n{topic_lines}\n\n"
            "GROUNDING: every factual claim you make must come from the topic you chose. Do not "
            "invent claims. How you frame the post — the opening, the format, whether you ask a "
            "question, whether you reference something topical — is entirely yours.\n"
        )
        prompt += _limit_instruction(channel).strip() + "\n\n" if _limit_instruction(channel) else "\n"

        trends = _trending_for_prompt()
        if trends:
            prompt += trends + "\n\n"

        if prior:
            m = prior.get("last_metrics", {})
            prompt += (
                "EVIDENCE from what has been published before. This is a record of what happened, "
                "not an instruction:\n"
                f"- A previous post read: \"{prior.get('winning_text', '')}\"\n"
                f"- It got {m.get('like_count', 0)} like(s), {m.get('repost_count', 0)} repost(s), "
                f"{m.get('reply_count', 0)} reply(ies), {m.get('bookmark_count', 0)} bookmark(s) "
                f"(engagement score {_score_of(m)}).\n"
            )
            if prior.get("human_feedback"):
                prompt += f"- A human reviewer said: \"{prior['human_feedback']}\"\n"
            prompt += ("You may extend this, improve on it, or deliberately go against it if you "
                       "can say why.\n\n")

        if controls and prior:
            prompt += (
                f"REQUIRED: {controls} of these {wanted} post(s) must deliberately "
                "CONTRADICT the evidence above — do the opposite of what it suggests works, on "
                "purpose. This is not a mistake and not a fallback. If the evidence is still "
                "true those posts will do worse and it is confirmed; if they do not do worse, "
                "the evidence has expired and we need to know. Mark them in EVIDENCE-AGAINST.\n\n"
            )

        prompt += "VOCABULARY. " + draft_labels.describe_for_prompt() + "\n\n"
        prompt += _target_instruction(target)
        prompt += (
            f"Reply with exactly {wanted} block(s) in this format, separated by a line of three "
            "dashes. Use these field names exactly:\n\n"
            "POST: the post text itself, nothing else\n"
            "TOPIC: which of the available topics above it is grounded in, copied exactly\n"
            "WHY: one sentence on why you chose that topic for this post\n"
            "IS: comma-separated characteristics of this post\n"
            "TESTING: one axis and this post's side of it, as `axis = side`\n"
            "EVIDENCE-USED: which evidence above you followed, or `none`\n"
            "EVIDENCE-AGAINST: which evidence you deliberately contradicted, or `none`\n"
            "---\n\n"
            "No numbering, no commentary outside the fields."
        )

        try:
            resp = requests.post(
                _LLM_ENDPOINT,
                json={"model": _LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.8},
                timeout=120,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
        except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError):
            return None

        drafts = []
        for block in _parse_blocks(content):
            text = _clean_llm_lines(block["POST"])
            text = text[0] if text else ""
            if not text:
                continue
            topic = _match_topic(block.get("TOPIC", ""), topics)
            if topic is None:
                # Dropped rather than guessed: a draft filed under a topic
                # that does not exist would corrupt the usage counts that
                # topic selection itself now reads.
                print(f"[dropped] A draft named topic {block.get('TOPIC', '')!r}, which is not "
                      "in this product's index — not queued.")
                continue
            if _enforce_constraints([text], channel) != [text]:
                continue
            axis, arm = _parse_axis(block.get("TESTING", ""))
            drafts.append({
                "id": f"draft-{uuid.uuid4().hex[:8]}", "channel": channel, "text": text,
                "status": "draft", "topic": topic["topic"], "brief": topic["brief"],
                "source": "topics",
                "topic_reason": block.get("WHY", ""),
                "characteristics": _split_labels(block.get("IS", "")),
                "test_axis": axis, "test_arm": arm,
                "evidence_used": block.get("EVIDENCE-USED", ""),
                "evidence_against": block.get("EVIDENCE-AGAINST", ""),
            })

        if not drafts:
            return None
        for problem in draft_labels.validate_group(drafts):
            # Reported, not rejected. A batch of 1 (the default) cannot
            # contain a contrast at all, so refusing here would mean never
            # generating anything; the operator needs to know the batch
            # proves nothing, which is different from it being unusable.
            print(f"[note] This batch tests nothing on one axis: {problem}")
        return drafts[:wanted]

    def revise_post(self, name: str, current_text: str, feedback: str, brief: str = "",
                    channel: str = "") -> str | None:
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
            "Rewrite the post applying that change." + _limit_instruction(channel) + " Still "
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
        lines = _enforce_constraints(_clean_llm_lines(content), channel)
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
                f"Its real results: {m.get('like_count', 0)} likes, "
                f"{m.get('repost_count', 0)} reposts, {m.get('reply_count', 0)} replies, "
                f"{m.get('bookmark_count', 0)} bookmarks "
                f"(engagement score {_score_of(m)}).\n"
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
                f"\nWrite {n} distinct, NEW short marketing posts for {channel}"
                + _limit_instruction(channel)
                + " They must actually apply that improvement and are not just a reword of the previous "
                "post. Still ground every claim in the source text above — do not invent claims not "
                "supported by it. Reply with exactly one post per line, no numbering, no extra commentary."
            )
        else:
            prompt += _target_instruction(target)
            prompt += (
                f"Write {n} distinct, short marketing posts for {channel}"
                + _limit_instruction(channel)
                + " They must reference concrete facts from the text above. Do not invent claims "
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

        lines = _enforce_constraints(_clean_llm_lines(content), channel)
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
