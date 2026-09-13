"""analysis -> DISCUSS (genuinely multi-model) -> next-round strategy.

Two *different* local models (not the same model talking to itself twice —
same-model self-debate mostly just agrees with itself and doesn't add real
cross-validation) each independently propose a content strategy for the
next post, grounded in analysis.py's verdict. A third pass — the cheaper
model, acting as judge — picks/merges them into one final instruction,
explicitly noting where the two proposals agreed or disagreed. Every call
is logged individually (audit.log_event) so the reasoning trail is
inspectable, not one opaque "AI said so" note.

Deliberately NOT an open-ended back-and-forth chat: unbounded multi-turn
debate between models multiplies cost/latency for real but hard-to-audit
gains. This is one bounded round — propose, propose, synthesize — enough
for real model diversity without unbounded cost. Defaults to the two
Ollama models already used elsewhere in this project (llama3.2:3b,
mistral:latest) so no extra download/setup is needed; override via
DISCUSS_MODEL_A / DISCUSS_MODEL_B / DISCUSS_JUDGE_MODEL if you have
better/different local models pulled.
"""
from __future__ import annotations

import json
import os

import requests

from . import audit
from .analysis import AnalysisResult

_ENDPOINT = os.environ.get("LLM_IMPROVE_ENDPOINT", "http://localhost:11434/v1/chat/completions")
_MODEL_A = os.environ.get("DISCUSS_MODEL_A", "llama3.2:3b")
_MODEL_B = os.environ.get("DISCUSS_MODEL_B", "mistral:latest")
_JUDGE_MODEL = os.environ.get("DISCUSS_JUDGE_MODEL", _MODEL_A)


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


def _proposal_prompt(product_name: str, channel: str, analysis: AnalysisResult) -> str:
    prompt = (
        f"You're advising on the next marketing post for '{product_name}' on {channel}.\n\n"
        f"Real performance analysis (not a guess): {analysis.evidence}\n"
        f"Trend: {analysis.trend}."
    )
    if analysis.human_note:
        prompt += f" A human reviewer added this note: {analysis.human_note}"
    prompt += (
        "\n\nIn 2-3 sentences, propose a specific, concrete strategy for the next post "
        "(what to change or keep, and why, based on the evidence above — not generic advice). "
        "No preamble."
    )
    return prompt


def synthesize_strategy(product_name: str, channel: str, analysis: AnalysisResult) -> str:
    """Returns the next round's content strategy. Never raises — any model
    call failing falls back to a clearly-labeled note (same pattern as the
    old improve.py), so a local-LLM hiccup never takes down the pipeline.
    """
    prompt = _proposal_prompt(product_name, channel, analysis)

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

    judge_prompt = (
        f"Two advisors proposed strategies for the next '{product_name}' post on {channel}, "
        f"given this real performance evidence: {analysis.evidence}\n\n"
        f"Advisor A ({_MODEL_A}): {proposal_a}\n\n"
        f"Advisor B ({_MODEL_B}): {proposal_b}\n\n"
        "In 2-3 sentences, give ONE final strategy: merge what they agree on, pick the "
        "stronger point where they disagree, and briefly say which it was (agreement or "
        "disagreement). No preamble."
    )
    synthesis = _complete(_JUDGE_MODEL, judge_prompt)
    audit.log_event("discuss", "synthesis", model=_JUDGE_MODEL, product=product_name, channel=channel,
                     synthesis=synthesis)
    if not synthesis:
        return f"[synthesis unavailable — judge model failed] A: {proposal_a} | B: {proposal_b}"
    return synthesis
