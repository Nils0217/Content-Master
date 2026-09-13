"""LLM-improve step: given a post's real engagement metrics, ask the local
LLM (same Ollama instance Cognee uses) for one concrete improvement for next
time. The result is stored in the Modiqo play record (modiqo_play.py) so the
next generation run for that product+channel has it as context.

Kept deliberately separate from cognee_client.py: this is a single stateless
completion call, not a knowledge-graph operation, and doesn't need Cognee's
auth/dataset machinery.
"""
from __future__ import annotations

import json
import os

import requests

DEFAULT_ENDPOINT = "http://localhost:11434/v1/chat/completions"
DEFAULT_MODEL = "llama3.2:3b"


def generate_improvement_note(
    product_name: str,
    channel: str,
    post_text: str,
    metrics: dict,
    endpoint: str | None = None,
    model: str | None = None,
) -> str:
    """Returns one short, concrete suggestion for the next post. Never
    raises — a local-LLM hiccup shouldn't take down the pipeline; on
    failure it returns a clearly-labeled fallback note instead.
    """
    endpoint = endpoint or os.environ.get("LLM_IMPROVE_ENDPOINT", DEFAULT_ENDPOINT)
    model = model or os.environ.get("LLM_IMPROVE_MODEL", DEFAULT_MODEL)

    prompt = (
        f"A marketing post for '{product_name}' on {channel} got these real results: "
        f"{metrics.get('like_count', 0)} likes, {metrics.get('repost_count', 0)} reposts, "
        f"{metrics.get('reply_count', 0)} replies.\n\n"
        f'Post text: "{post_text}"\n\n'
        "In one short sentence, suggest one concrete, specific change to try on the "
        "next post for this product/channel. No preamble, just the suggestion."
    )
    try:
        resp = requests.post(
            endpoint,
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        return content
    except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError) as e:
        return f"[improve step unavailable: {e}]"
