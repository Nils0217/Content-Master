"""Layer 4 — RocketRide.ai: motion/orchestration. Turns "what the agent knows"
(HydraDB graph + hotdata metrics) into "what the agent does" (drafts, then —
after human approval — real posts).

Verified working against your account (Chen's Workspace) on 2026-09-11:
connect + auth both succeeded with the API key in .env.

`run_pipe()` calls a real RocketRide `.pipe` pipeline (built/exported from
RocketRide Cloud's editor — see the hackathon setup guide). Until you've
built one, `draft_posts()` generates the same shape of output locally so
the rest of the loop (human approval -> hotdata metrics -> Modiqo capture)
has something real to run against; swap it for `run_pipe()` once a
pipeline exists.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

import requests
from rocketride import RocketRideClient

from .config import RocketRideSettings, settings

_LLM_ENDPOINT = os.environ.get("LLM_IMPROVE_ENDPOINT", "http://localhost:11434/v1/chat/completions")
_LLM_MODEL = os.environ.get("LLM_IMPROVE_MODEL", "llama3.2:3b")


class RocketRide:
    def __init__(self, cfg: RocketRideSettings | None = None):
        self.cfg = cfg or settings.rocketride

    async def account_info(self) -> dict[str, Any]:
        async with RocketRideClient(uri=self.cfg.uri, auth=self.cfg.api_key) as c:
            return c.get_account_info()

    async def run_pipe(self, filepath: str, **params: Any) -> dict[str, Any]:
        """Execute a real .pipe pipeline exported from RocketRide Cloud."""
        async with RocketRideClient(uri=self.cfg.uri, auth=self.cfg.api_key) as c:
            return await c.use(filepath=filepath, args=[f"{k}={v}" for k, v in params.items()])

    def draft_posts(
        self,
        product: dict[str, Any],
        channel: str,
        n: int = 3,
        context: str = "",
        prior: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Local stand-in for a RocketRide content-generation pipe: turns the
        Cognee/HydraDB product graph into N draft posts for one channel.

        `context` is Cognee's actual extracted text about the source
        document (see pipeline.py step1_cognee_extract) — when present this
        calls the local LLM to write posts grounded in that real content.
        Without it (Cognee down, or no context passed), falls back to the
        old fixed-template generator so the rest of the loop never breaks.

        `prior` (a Modiqo play record — see modiqo_play.find_play) is the
        last winning post + its real metrics + the LLM's own improvement
        note for this product/channel. When present, this run doesn't just
        replay the old text: it writes a genuinely new version that acts on
        that specific feedback — the "another loop" from the white paper.
        """
        name = product.get("name", "the product")
        if context.strip():
            drafts = self._llm_draft_posts(name, channel, n, context, prior)
            if drafts:
                return drafts

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
                }
            )
        return drafts

    def _llm_draft_posts(
        self, name: str, channel: str, n: int, context: str, prior: dict[str, Any] | None = None
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
                f"A review of that result suggested this specific improvement: "
                f"\"{prior.get('improvement_note', '')}\"\n\n"
                f"Write {n} distinct, NEW short marketing posts for {channel} (under 280 characters "
                "each) that actually apply that improvement and are not just a reword of the previous "
                "post. Still ground every claim in the source text above — do not invent claims not "
                "supported by it. Reply with exactly one post per line, no numbering, no extra commentary."
            )
        else:
            prompt += (
                f"Write {n} distinct, short marketing posts for {channel} (under 280 characters "
                "each) that reference concrete facts from the text above. Do not invent claims "
                "not supported by the text. Reply with exactly one post per line, no numbering, "
                "no extra commentary."
            )
        try:
            resp = requests.post(
                _LLM_ENDPOINT,
                json={"model": _LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.7},
                timeout=90,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
        except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError):
            return None

        lines = [line.strip("-* \t") for line in content.splitlines() if line.strip()]
        # Small local models often ignore "no extra commentary" and add a
        # one-line preamble ("Here are N posts:") before the real content —
        # drop lines that look like a header/preamble rather than a post.
        lines = [
            line for line in lines
            if not (line.endswith(":") or line.lower().startswith(("here are", "here's", "sure,", "certainly")))
        ]
        if not lines:
            return None
        drafts = []
        for i in range(n):
            text = lines[i % len(lines)]
            drafts.append(
                {"id": f"draft-{uuid.uuid4().hex[:8]}", "channel": channel, "text": text, "status": "draft"}
            )
        return drafts
