"""Loads .env and exposes typed settings for every layer of the pipeline."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class CogneeSettings:
    base_url: str = field(default_factory=lambda: os.environ.get("COGNEE_BASE_URL", "http://localhost:8000"))
    email: str = field(default_factory=lambda: os.environ.get("COGNEE_EMAIL", ""))
    password: str = field(default_factory=lambda: os.environ.get("COGNEE_PASSWORD", ""))
    dataset: str = field(default_factory=lambda: os.environ.get("COGNEE_DATASET", "contentmaster"))
    llm_api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY", ""))


@dataclass(frozen=True)
class BlueskySettings:
    handle: str = field(default_factory=lambda: os.environ.get("BLUESKY_HANDLE", ""))
    # repr=False: keeps the app password out of any accidental print(settings)/
    # repr(settings) or unhandled-exception traceback that includes locals.
    app_password: str = field(default_factory=lambda: os.environ.get("BLUESKY_APP_PASSWORD", ""), repr=False)

    @property
    def configured(self) -> bool:
        return bool(self.handle and self.app_password)


@dataclass(frozen=True)
class CloudflareSettings:
    """Cloudflare Workers AI — image generation (Phase 4, 2026-09-16). Free
    tier: 10,000 Neurons/day (~173 1024x1024 images), no expiry; picked
    over Replicate/OpenAI/Stability for this reason at this project's
    volume — see docs/LOG.md 2026-09-16 for the comparison.
    """
    account_id: str = field(default_factory=lambda: os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""))
    api_token: str = field(default_factory=lambda: os.environ.get("CLOUDFLARE_API_TOKEN", ""), repr=False)

    @property
    def configured(self) -> bool:
        return bool(self.account_id and self.api_token)


@dataclass(frozen=True)
class Settings:
    cognee: CogneeSettings = field(default_factory=CogneeSettings)
    bluesky: BlueskySettings = field(default_factory=BlueskySettings)
    cloudflare: CloudflareSettings = field(default_factory=CloudflareSettings)
    project_root: Path = PROJECT_ROOT


settings = Settings()
