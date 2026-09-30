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
from typing import Any

import requests

from . import audit
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


def _proposal_prompt(
    product_name: str, channel: str, analysis: AnalysisResult, target: dict[str, Any] | None = None,
) -> str:
    prompt = f"You're advising on the next marketing post for '{product_name}' on {channel}.\n\n"
    if analysis.has_baseline:
        prompt += (f"Real performance analysis (not a guess): {analysis.evidence}\n"
                   f"Trend: {analysis.trend}.")
    else:
        # 2026-09-24: a different question, not a silenced one.
        #
        # This prompt used to be the only one, asked whether or not there
        # was anything to analyse. With one prior post the advisors were
        # told "here is the real performance analysis" and given a delta
        # computed from n=1, so they answered confidently about a trend
        # that did not exist — and, in a real session, recommended chasing
        # a meme ("cat in the hat trend") on that basis.
        #
        # Producing nothing instead would be worse: a baseline needs eight
        # posts, so the system would have no direction during exactly the
        # period it has to operate. docs/SCHEDULE.md Phase 10 already said
        # what a failed gate should produce — a falsifiable hypothesis —
        # and this is where that becomes real. The ask changes from "what
        # do the numbers say to do" to "what is worth testing first, and
        # how would we know it was wrong".
        prompt += (
            f"{analysis.evidence}\n\n"
            "There is no performance history to reason from yet, so do not describe any "
            "result as proven, improving or declining, and do not infer anything from this "
            "single post's score."
        )
    if analysis.human_note:
        prompt += f" A human reviewer added this note: {analysis.human_note}"
    if analysis.external_signal:
        prompt += f"\n\n{analysis.external_signal}"
    if analysis.trending_context:
        prompt += f"\n\n{analysis.trending_context}"
    region, audience = (target or {}).get("region"), (target or {}).get("audience")
    if region or audience:
        # 2026-09-18: without this, a real session found the models citing
        # the trending context's generic top regions (Romania/Indonesia/
        # Peru, from Google Trends, unrelated to this product's actual
        # market) as if they were meaningful targeting advice. This is the
        # product's actual, deliberately chosen target, and takes priority
        # over anything in the trending context above.
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
        )
    else:
        prompt += (
            "\n\nPropose ONE thing worth testing first, as a hypothesis. Two or three "
            "sentences, in this shape:\n"
            "- what to try, concretely enough that a writer could act on it\n"
            "- why you think it might work\n"
            "- what result would show you were WRONG — a specific outcome that would rule "
            "this direction out\n"
            "That last part is not optional. A direction nothing could disprove cannot be "
            "tested, and testing it is the whole reason for the suggestion. "
        )
    prompt += (
        "If the trending context above has a genuinely relevant term or angle, you may "
        "reference it, but don't force one in if nothing fits — a trend unrelated to this "
        "product is a distraction, not an opportunity. "
        f"The product name and brand ('{product_name}') is fixed and not up for discussion — "
        "propose a content theme, angle, or wording change, never a rename or rebrand. No preamble."
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


def synthesize_strategy(
    product_name: str, channel: str, analysis: AnalysisResult, target: dict[str, Any] | None = None,
) -> str:
    """Returns the next round's content strategy. Never raises — any model
    call failing falls back to a clearly-labeled note (same pattern as the
    old improve.py), so a local-LLM hiccup never takes down the pipeline.

    `target` (2026-09-18, see topic_index.load_target): passed through to
    _proposal_prompt so a product with a real target doesn't get advice
    grounded in the trending context's generic top regions instead.
    """
    prompt = _proposal_prompt(product_name, channel, analysis, target)

    proposal_a = _complete(_MODEL_A, prompt)
    audit.log_event("discuss", "proposal", model=_MODEL_A, product=product_name, channel=channel,
                     proposal=proposal_a)
    proposal_b = _complete(_MODEL_B, prompt)
    audit.log_event("discuss", "proposal", model=_MODEL_B, product=product_name, channel=channel,
                     proposal=proposal_b)

    proposals = [p for p in (proposal_a, proposal_b) if p]
    if not proposals:
        fallback = f"[discuss step unavailable: both {_MODEL_A} and {_MODEL_B} failed to respond]"
        audit.log_event("discuss", "fallback", product=product_name, channel=channel, note=fallback)
        return fallback
    if len(proposals) == 1:
        note = proposals[0] + " (only one model responded — not cross-validated against a second.)"
        audit.log_event("discuss", "single_model_only", product=product_name, channel=channel, note=note)
        return note

    if not _JUDGE_ENABLED:
        # 2026-09-19 (code scan): this used to return a string opening
        # with "[judge step disabled, see discuss.py _JUDGE_ENABLED ...]"
        # and naming both model ids. That string is stored as the play's
        # `improvement_note`, and modiqo_play.find_best_prior() feeds it
        # straight into the NEXT run's draft prompt under the heading "An
        # LLM review of that result suggested this specific improvement:"
        # — so the generating model was being handed this project's
        # internal config flag names and Ollama model ids as if they were
        # marketing advice. The which-models/why-not-merged detail belongs
        # in the audit trail, not in a prompt; "Advisor A / Advisor B" is
        # already enough for a human to see these are two unmerged
        # opinions rather than one verdict.
        note = f"Advisor A: {proposal_a}\n\nAdvisor B: {proposal_b}"
        audit.log_event("discuss", "judge_skipped", product=product_name, channel=channel,
                         model_a=_MODEL_A, model_b=_MODEL_B, note=note)
        return note

    judge_prompt = (
        f"Two advisors proposed strategies for the next '{product_name}' post on {channel}, "
        f"given this real performance evidence: {analysis.evidence}\n\n"
        # Model ids deliberately absent: this prompt's answer becomes the
        # `improvement_note`, which is fed verbatim into the next drafting
        # prompt, and a judge that echoes "Advisor A (llama3.2:3b)" puts
        # this project's Ollama config into what reads as marketing
        # advice. The ids are in the audit log, which is where they belong.
        f"Advisor A: {proposal_a}\n\n"
        f"Advisor B: {proposal_b}\n\n"
        "In 2-3 sentences, give ONE final strategy: merge what they agree on, pick the "
        "stronger point where they disagree, and briefly say which it was (agreement or "
        "disagreement). "
        f"The product name and brand ('{product_name}') is fixed and not up for discussion — "
        "the final strategy must be a content theme, angle, or wording change, never a rename "
        "or rebrand. No preamble."
    )
    synthesis = _clean_synthesis(_complete(_JUDGE_MODEL, judge_prompt))
    audit.log_event("discuss", "synthesis", model=_JUDGE_MODEL, product=product_name, channel=channel,
                     synthesis=synthesis)
    if not synthesis:
        # Same reasoning as the judge-disabled branch above: no bracketed
        # internal status text, because this value is fed back into the
        # next run's generation prompt.
        audit.log_event("discuss", "synthesis_failed", product=product_name, channel=channel,
                         model=_JUDGE_MODEL)
        return f"Advisor A: {proposal_a}\n\nAdvisor B: {proposal_b}"
    return synthesis
