"""analysis -> DISCUSS (genuinely multi-model) -> next-round strategy.

Two *different* local models (not the same model talking to itself twice —
same-model self-debate mostly just agrees with itself and doesn't add real
cross-validation) each independently propose a content strategy for the
next post, grounded in analysis.py's verdict. Every call is logged
individually (audit.log_event) so the reasoning trail is inspectable, not
one opaque "AI said so" note.

Deliberately NOT an open-ended back-and-forth chat: unbounded multi-turn
debate between models multiplies cost/latency for real but hard-to-audit
gains. This is one bounded round — propose, propose, (optionally)
synthesize — enough for real model diversity without unbounded cost.
Defaults to the two Ollama models already used elsewhere in this project
(llama3.2:3b, mistral:latest) so no extra download/setup is needed;
override via DISCUSS_MODEL_A / DISCUSS_MODEL_B if you have
better/different local models pulled.

A third model then picks between the two proposals and says where they
agreed or disagreed. This was off from 2026-09-18 to 2026-09-25, because
the judge defaulted to Advisor A's own model — "llama grades its own
proposal against mistral's" is not independent judgment, and a merge
produced that way reads more authoritative than it is. It is on now that
qwen3:4b, a third family, does the judging; set
DISCUSS_JUDGE_ENABLED=0 to go back to returning both proposals unmerged.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import requests

from . import audit, hypothesis as hyp, reasoning
from .analysis import AnalysisResult

_ENDPOINT = os.environ.get("LLM_IMPROVE_ENDPOINT", "http://localhost:11434/v1/chat/completions")
_MODEL_A = os.environ.get("DISCUSS_MODEL_A", "llama3.2:3b")
_MODEL_B = os.environ.get("DISCUSS_MODEL_B", "mistral:latest")
# 2026-09-25: a real third model, so the judge is no longer Advisor A
# marking its own homework. Chosen by running the actual judge prompt
# through every locally pulled model:
#
#   qwen3:4b        21s   415 chars  named the disagreement, picked a side,
#                                    said why  ← only one that did
#   qwen2.5:7b      26s   314 chars  merged, claimed to pick "the stronger
#                                    point" without saying which
#   gemma3:4b       16s   328 chars  merged both, no choice made
#   phi4-mini-      80s  7844 chars  emitted its <think> monologue and was
#     reasoning                      cut off before reaching a conclusion
#
# phi was the obvious candidate on paper — it is the only reasoning-tuned
# model here — and was the worst in practice. A judge's output becomes the
# `improvement_note`, which modiqo_play.find_best_prior() feeds verbatim
# into the next drafting prompt, so a model that thinks out loud puts
# 8000 characters of deliberation where one sentence of verdict belongs.
# Merging is not judging: three of the four produced a blend of both
# proposals, which is what this step already did for free by concatenating
# them.
#
# Also a different family from both advisors (llama3.2, mistral), which
# was the original requirement — a same-family judge mostly agrees with
# whichever advisor it resembles.
_JUDGE_MODEL = os.environ.get("DISCUSS_JUDGE_MODEL", "qwen3:4b")

# 2026-09-18 (docs/LOG.md — real design review): off by default. Without
# DISCUSS_JUDGE_MODEL set, the judge is the same model as Advisor A, so the
# "third pass" this module's own docstring describes was really "model A
# judges its own proposal against mistral's", not genuine third-party
# judgment — misleading enough that a synthesized "final strategy" isn't
# trustworthy on its own right now. Left in and easy to flip back on
# (DISCUSS_JUDGE_ENABLED=1) once a real third-party model is wired up for
# DISCUSS_JUDGE_MODEL; until then synthesize_strategy() returns both raw
# proposals, labeled, instead of a fake merge.
_JUDGE_ENABLED = os.environ.get("DISCUSS_JUDGE_ENABLED", "1") == "1"


def _complete(model: str, prompt: str, timeout: int = 90) -> str | None:
    try:
        resp = requests.post(
            _ENDPOINT,
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.7},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError):
        return None


def _grounding(product_name: str, post_text: str, category: str,
               topic_names: list[str] | None) -> str:
    """What the account is and what it can write about.

    2026-10-04. The advisors used to get the product name and nothing else,
    so with no baseline their only material was the Google Trends list —
    all eight 7d hypotheses chased "cat in the hat" or Romania, and one
    called Fluffy roommate a cat *breed*. The drafting prompt already said
    what the subject is (draft_generator._subject_description); this one
    did not, which is the same idea living only where it was born.
    """
    from .draft_generator import _subject_description

    out = _subject_description(product_name, category) + "\n\n"
    if topic_names:
        out += ("THE SOURCE DOCUMENT'S SECTIONS — what this account can write about, and "
                "nothing else:\n" + "; ".join(topic_names) + "\n\n")
    if post_text:
        out += f"The post being reviewed said: \"{post_text}\"\n\n"
    return out


def _proposal_prompt(
    product_name: str, channel: str, analysis: AnalysisResult, target: dict[str, Any] | None = None,
    post_text: str = "", category: str = "", topic_names: list[str] | None = None,
) -> str:
    prompt = f"You're advising on the next post for '{product_name}' on {channel}.\n\n"
    prompt += _grounding(product_name, post_text, category, topic_names)
    if analysis.has_baseline:
        prompt += (f"Real performance analysis (not a guess): {analysis.evidence}\n"
                   f"Trend: {analysis.trend}.")
    else:
        # 2026-09-24: a different question, not a silenced one. A baseline
        # needs eight posts, so producing nothing here would leave the
        # system with no direction during exactly the period it has to
        # operate. The ask is "what is worth testing", not "what do the
        # numbers say" — with one prior post the advisors used to be handed
        # a delta computed from n=1 and answered confidently about a trend
        # that did not exist.
        prompt += (
            f"{analysis.evidence}\n\n"
            "There is no performance history yet, so do not describe any result as proven, "
            "improving or declining, and do not infer anything from this single post's score. "
            "You know nothing about who follows this account or where they are."
        )
    if analysis.human_note:
        prompt += f" A human reviewer added this note: {analysis.human_note}"
    if analysis.external_signal:
        prompt += f"\n\n{analysis.external_signal}"
    if analysis.trending_context:
        prompt += f"\n\n{analysis.trending_context}"
    region, audience = (target or {}).get("region"), (target or {}).get("audience")
    if region or audience:
        # 2026-09-18: without this, the models cited the trending context's
        # generic top regions as if they were this product's market.
        parts = [p for p in (f"region: {region}" if region else "", f"audience: {audience}" if audience else "") if p]
        prompt += (
            f"\n\nThis product's actual target is {', '.join(parts)}. Prefer this over any "
            "region or audience mentioned in the trending context above if they conflict; "
            "the trending context is general search interest, not this product's chosen market."
        )
    if analysis.has_baseline:
        prompt += (
            "\n\nIn 2-3 sentences, propose a specific, concrete strategy for the next post "
            "(what to change or keep, and why, based on the evidence above — not generic advice). "
            "If the trending context above has a genuinely relevant term or angle, you may "
            "reference it, but don't force one in if nothing fits. "
            f"The name '{product_name}' is fixed — propose a content theme, angle, or wording "
            "change, never a rename or rebrand. No preamble."
        )
    else:
        # 2026-10-04: no "what would show you were WRONG". A change that does
        # not move its number did not suit the conditions it went out under
        # — the follower count, the day, the moment — which the system
        # records at publish. It is not a verdict on the idea. And WATCH is
        # limited to what the system can read: the first real run's
        # falsifiers named ad clicks, bounce rate and regional engagement.
        prompt += (
            "\n\nPropose ONE thing worth testing in the next post. Answer with exactly these "
            "three lines and nothing else:\n"
            "TEST: one change to how the post is written or what it is about, concrete enough "
            "that a writer could act on it\n"
            "WHY: one sentence on why it might suit this audience right now\n"
            "WATCH: the one number that will show whether it suited — "
            + ", ".join(hyp.METRICS[:-1]) + f" or {hyp.METRICS[-1]}. These four are the only "
            "numbers this system can see.\n"
            "There is no right or wrong outcome. If the number does not move, that means the "
            "change did not suit the conditions at the time, not that the idea was bad.\n"
            "A trend unrelated to what this account writes about is a distraction, not an "
            f"opportunity. The name '{product_name}' is fixed — never propose a rename."
        )
    return prompt


# A verdict, not an essay. Anything longer is a model thinking out loud,
# and this value is fed straight into the next drafting prompt.
_MAX_SYNTHESIS_CHARS = 1200
_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
_OPEN_THINK = re.compile(r"<think>.*", re.DOTALL | re.IGNORECASE)


def _clean_synthesis(text: str | None) -> str | None:
    """Strip a reasoning model's visible deliberation, and refuse a verdict
    that is not one.

    2026-09-25, found by trialling every local model on the real judge
    prompt: phi4-mini-reasoning returned 7844 characters of `<think>`
    monologue and was cut off before it reached a conclusion. Any
    reasoning-tuned model swapped in via DISCUSS_JUDGE_MODEL can do the
    same, so this guards the shape rather than the model. Returning None
    falls back to both proposals unmerged, which is worse than a good
    synthesis and much better than pasting a model's inner monologue into
    the next post's instructions.
    """
    if not text:
        return None
    cleaned = _THINK_BLOCK.sub("", text)
    cleaned = _OPEN_THINK.sub("", cleaned).strip()
    if not cleaned or len(cleaned) > _MAX_SYNTHESIS_CHARS:
        return None
    return cleaned


@dataclass
class Strategy:
    """What one analysis produced for the next post.

    `text` is what history stores as `improvement_note` and the review
    queue shows. In hypothesis mode it is hypothesis.render() of the chosen
    proposal — never a judge's rewrite of it. `reasoning_id` finds every
    step that led here in plays/_reasoning.jsonl.
    """
    text: str
    hypothesis: dict[str, Any] | None
    reasoning_id: str


def synthesize_strategy(
    product_name: str, channel: str, analysis: AnalysisResult, target: dict[str, Any] | None = None,
    *, post_id: str | None = None, checkpoint: str | None = None, post_text: str = "",
    category: str = "", topic_names: list[str] | None = None,
) -> Strategy:
    """The next round's direction. Never raises — a model call failing is
    recorded and shown, so a local-LLM hiccup never takes down the pipeline.

    Every model call — prompt, raw reply, what was parsed — is recorded in
    plays/_reasoning.jsonl under one reasoning_id, tied to the post and
    checkpoint it analysed.
    """
    rid = reasoning.new_id()
    log = dict(product=product_name, channel=channel, post_id=post_id, checkpoint=checkpoint,
               reasoning_id=rid)
    prompt = _proposal_prompt(product_name, channel, analysis, target, post_text=post_text,
                              category=category, topic_names=topic_names)
    steps: list[dict[str, Any]] = []
    raw: dict[str, str | None] = {}
    for role, model in (("A", _MODEL_A), ("B", _MODEL_B)):
        raw[role] = _complete(model, prompt)
        audit.log_event("discuss", "proposal", model=model, role=role, proposal=raw[role], **log)
        steps.append({"step": "advisor", "role": role, "model": model, "prompt": prompt,
                      "raw": raw[role]})

    if analysis.has_baseline:
        text, outcome = _evidence_strategy(product_name, channel, analysis, raw, steps, log)
        h = None
    else:
        h, text, outcome = _hypothesis(product_name, channel, raw, steps, log)

    reasoning.record(rid, post_id=post_id, checkpoint=checkpoint, product=product_name,
                     channel=channel, mode="evidence" if analysis.has_baseline else "hypothesis",
                     steps=steps, outcome=outcome, result_text=text, hypothesis=h)
    return Strategy(text=text, hypothesis=h, reasoning_id=rid)


def _unmerged(raw: dict[str, str | None]) -> str:
    return "\n\n".join(f"Advisor {r}: {t}" for r, t in raw.items() if t)


def _hypothesis(product_name: str, channel: str, raw: dict[str, str | None],
                steps: list[dict[str, Any]], log: dict[str, Any],
                ) -> tuple[dict[str, Any] | None, str, str]:
    """Parse both proposals, let the judge pick one, return it unchanged."""
    parsed = {}
    for step in steps:
        step["parsed"] = hyp.parse_hypothesis(step["raw"])
        if step["parsed"]:
            parsed[step["role"]] = {**step["parsed"], "model": step["model"]}

    if not any(raw.values()):
        audit.log_event("discuss", "fallback", note="no advisor responded", **log)
        return None, f"[no hypothesis: neither {_MODEL_A} nor {_MODEL_B} responded]", "no_response"
    if not parsed:
        # Replied, but not in a shape we could read. Said, not swallowed:
        # an empty parse and "nothing to say" must not look the same.
        audit.log_event("discuss", "unparsed", **log)
        print("[note] Neither advisor's reply had a TEST line — no hypothesis this round. "
              "Both replies are kept in plays/_reasoning.jsonl.")
        return None, _unmerged(raw), "none_usable"

    if len(parsed) == 1 or not _JUDGE_ENABLED:
        role = next(iter(parsed))
        why = ("only one advisor gave a readable test" if len(parsed) == 1
               else "judge disabled — first readable test used")
        chosen = {**parsed[role], "picked": role, "pick_reason": why}
        outcome = "only_one_usable" if len(parsed) == 1 else "judge_disabled"
    else:
        judge_prompt = (
            f"Two advisors each proposed one test for the next post on the {channel} account "
            f"'{product_name}'. There is no performance history yet.\n\n"
            f"Advisor A:\n{hyp.render(parsed['A'])}\n\nAdvisor B:\n{hyp.render(parsed['B'])}\n\n"
            "Pick the ONE test more worth running next: the one that changes a single clear "
            "thing and stays within what the account writes about. Do not merge them and do "
            "not rewrite them. Answer with exactly these two lines and nothing else:\n"
            "PICK: A or B\nREASON: one sentence"
        )
        judge_raw = _complete(_JUDGE_MODEL, judge_prompt, timeout=180)
        cleaned = _clean_synthesis(judge_raw)
        pick, reason = hyp.parse_pick(cleaned)
        steps.append({"step": "judge", "role": "judge", "model": _JUDGE_MODEL,
                      "prompt": judge_prompt, "raw": judge_raw,
                      "parsed": {"pick": pick, "reason": reason} if pick else None})
        audit.log_event("discuss", "judged", model=_JUDGE_MODEL, pick=pick, reason=reason,
                        raw=judge_raw, **log)
        if pick:
            chosen = {**parsed[pick], "picked": pick, "pick_reason": reason}
            outcome = "picked"
        else:
            # Recorded as exactly that, so it is visible, and A is used
            # rather than nothing — a test is still better than none.
            chosen = {**parsed["A"], "picked": "A",
                      "pick_reason": "the judge gave no readable pick — Advisor A used by default"}
            outcome = "judge_unusable"

    chosen["id"] = hyp.new_id()
    return chosen, hyp.render(chosen), outcome


def _evidence_strategy(product_name: str, channel: str, analysis: AnalysisResult,
                       raw: dict[str, str | None], steps: list[dict[str, Any]],
                       log: dict[str, Any]) -> tuple[str, str]:
    proposals = [t for t in raw.values() if t]
    if not proposals:
        fallback = f"[discuss step unavailable: both {_MODEL_A} and {_MODEL_B} failed to respond]"
        audit.log_event("discuss", "fallback", note=fallback, **log)
        return fallback, "no_response"
    if len(proposals) == 1:
        note = proposals[0] + " (only one model responded — not cross-validated against a second.)"
        audit.log_event("discuss", "single_model_only", note=note, **log)
        return note, "single_model"
    if not _JUDGE_ENABLED:
        # Model ids stay out of this text: it is stored as the play's
        # `improvement_note` and read back into prompts. "Advisor A / B" is
        # enough for a human; the ids are in the audit trail.
        note = _unmerged(raw)
        audit.log_event("discuss", "judge_skipped", model_a=_MODEL_A, model_b=_MODEL_B,
                        note=note, **log)
        return note, "unmerged"

    judge_prompt = (
        f"Two advisors proposed strategies for the next '{product_name}' post on {channel}, "
        f"given this real performance evidence: {analysis.evidence}\n\n"
        f"Advisor A: {raw['A']}\n\n"
        f"Advisor B: {raw['B']}\n\n"
        "In 2-3 sentences, give ONE final strategy: merge what they agree on, pick the "
        "stronger point where they disagree, and briefly say which it was (agreement or "
        "disagreement). "
        f"The product name and brand ('{product_name}') is fixed and not up for discussion — "
        "the final strategy must be a content theme, angle, or wording change, never a rename "
        "or rebrand. No preamble."
    )
    judge_raw = _complete(_JUDGE_MODEL, judge_prompt)
    synthesis = _clean_synthesis(judge_raw)
    steps.append({"step": "judge", "role": "judge", "model": _JUDGE_MODEL, "prompt": judge_prompt,
                  "raw": judge_raw, "parsed": synthesis})
    audit.log_event("discuss", "synthesis", model=_JUDGE_MODEL, synthesis=synthesis, **log)
    if not synthesis:
        audit.log_event("discuss", "synthesis_failed", model=_JUDGE_MODEL, **log)
        return _unmerged(raw), "unmerged"
    return synthesis, "synthesized"
