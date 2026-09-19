"""Layer — image generation (white paper §3 Phase 4, 2026-09-16).

Cloudflare Workers AI running FLUX.1-schnell — picked over Replicate/
Stability/OpenAI for this project's actual volume: 10,000 free Neurons/day
(a 1024x1024 image costs 57.6 Neurons, so ~173 free images/day, resets
daily, no expiry), which realistically means $0 ongoing cost for a
handful of images per post. See docs/LOG.md 2026-09-16 for the comparison
against the other options.

Silently disabled (returns None, never raises) when
CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN aren't set — same "optional,
graceful degradation" pattern as every other external dependency in this
project (Bluesky, Google Trends). A run without Cloudflare configured
just publishes text-only, exactly like today.
"""
from __future__ import annotations

import base64
import json

import requests

from .config import settings

_API_URL_TEMPLATE = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/black-forest-labs/flux-1-schnell"


def generate_image(prompt: str, seed: int | None = None) -> bytes | None:
    """One image, one prompt, one call. Returns raw PNG bytes, or None if
    Cloudflare isn't configured or the call fails for any reason — the
    caller (pipeline.py) always has a text-only fallback.
    """
    if not settings.cloudflare.configured:
        return None

    body: dict[str, object] = {"prompt": prompt}
    if seed is not None:
        body["seed"] = seed

    try:
        resp = requests.post(
            _API_URL_TEMPLATE.format(account_id=settings.cloudflare.account_id),
            headers={"Authorization": f"Bearer {settings.cloudflare.api_token}"},
            json=body,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, json.JSONDecodeError):
        return None

    if not data.get("success"):
        return None
    image_b64 = (data.get("result") or {}).get("image")
    if not image_b64:
        return None
    try:
        return base64.b64decode(image_b64)
    except (ValueError, TypeError):
        return None
