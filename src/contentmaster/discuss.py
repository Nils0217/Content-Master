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

A third pass can pick/merge the two proposals into one final instruction,
explicitly noting where they agreed or disagreed, but that's OFF by
default (2026-09-18, see _JUDGE_ENABLED below): without a genuinely
different third model configured, the "judge" defaults to re-running
Advisor A on its own proposal, which isn't real independent judgment.
synthesize_strategy() returns both raw proposals, labeled, until a real
third-party model is wired up via DISCUSS_JUDGE_MODEL and
DISCUSS_JUDGE_ENABLED=1.
"""
from __future__ import annotations

import json
import os
from typing import Any

import requests

from . import audit
from .analysis import AnalysisResult

_ENDPOINT = os.environ.get("LLM_IMPROVE_ENDPOINT", "http://localhost:11434/v1/chat/completions")
_MODEL_A = os.environ.get("DISCUSS_MODEL_A", "llama3.2:3b")
_MODEL_B = os.environ.get("DISCUSS_MODEL_B", "mistral:latest")
_JUDGE_MODEL = os.environ.get("DISCUSS_JUDGE_MODEL", _MODEL_A)

# 2026-09-18 (docs/LOG.md — real design review): off by default. Without
# DISCUSS_JUDGE_MODEL set, the judge is the same model as Advisor A, so the
# "third pass" this module's own docstring describes was really "model A
# judges its own proposal against mistral's", not genuine third-party
# judgment — misleading enough that a synthesized "final strategy" isn't
# trustworthy on its own right now. Left in and easy to flip back on
# (DISCUSS_JUDGE_ENABLED=1) once a real third-party model is wired up for
# DISCUSS_JUDGE_MODEL; until then synthesize_strategy() returns both raw
# proposals, labeled, instead of a fake merge.
_JUDGE_ENABLED = os.environ.get("DISCUSS_JUDGE_ENABLED", "0") == "1"


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
    prompt = (
        f"You're advising on the next marketing post for '{product_name}' on {channel}.\n\n"
        f"Real performance analysis (not a guess): {analysis.evidence}\n"
        f"Trend: {analysis.trend}."
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
    prompt += (
        "\n\nIn 2-3 sentences, propose a specific, concrete strategy for the next post "
        "(what to change or keep, and why, based on the evidence above — not generic advice). "
        "If the trending context above has a genuinely relevant term or angle, you may "
        "reference it, but don't force one in if nothing fits. "
        f"The product name and brand ('{product_name}') is fixed and not up for discussion — "
        "propose a content theme, angle, or wording change, never a rename or rebrand. No preamble."
    )
    return prompt


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
        f"Advisor A ({_MODEL_A}): {proposal_a}\n\n"
        f"Advisor B ({_MODEL_B}): {proposal_b}\n\n"
        "In 2-3 sentences, give ONE final strategy: merge what they agree on, pick the "
        "stronger point where they disagree, and briefly say which it was (agreement or "
        "disagreement). "
        f"The product name and brand ('{product_name}') is fixed and not up for discussion — "
        "the final strategy must be a content theme, angle, or wording change, never a rename "
        "or rebrand. No preamble."
    )
    synthesis = _complete(_JUDGE_MODEL, judge_prompt)
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
