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

import uuid
from typing import Any

from rocketride import RocketRideClient

from .config import RocketRideSettings, settings


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

    def draft_posts(self, product: dict[str, Any], channel: str, n: int = 3) -> list[dict[str, Any]]:
        """Local stand-in for a RocketRide content-generation pipe: turns the
        Cognee/HydraDB product graph into N draft posts for one channel.
        """
        name = product.get("name", "the product")
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
