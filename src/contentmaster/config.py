"""Loads .env and exposes typed settings for every layer of the pipeline."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _resolve_data_root() -> Path:
    """Where everything the pipeline *writes* lives — plays/, metrics/,
    audit/, generated_images/. Defaults to PROJECT_ROOT, i.e. unchanged
    behavior; set CONTENTMASTER_DATA_ROOT to point a run somewhere else.

    This is how test runs are kept out of the real ledger (2026-09-19,
    decided after `ZZ_DISPOSABLE_TEST` and `Wiring Test Product` rows were
    found sitting in plays/_history.jsonl and plays/_tracking_review.jsonl
    alongside real posts). The alternative — an `is_test` column filtered
    at read time — was rejected: docs/SCHEDULE.md Phase 10 lists four
    separate places history gets read, two of them dbt models, and a
    filter applied in Python but not in dbt leaks silently with no error.
    A directory has no such failure mode: code that never opens the file
    cannot read the wrong rows out of it.

    Note this deliberately does NOT cover *inputs* (whitepapers,
    warehouse/local.duckdb) — those are shared, and a test run reading the
    real whitepaper is fine. Only writes are isolated.
    """
    raw = os.environ.get("CONTENTMASTER_DATA_ROOT", "").strip()
    if not raw:
        return PROJECT_ROOT
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


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
    # Read inputs from project_root; write outputs under data_root. They
    # are the same directory unless CONTENTMASTER_DATA_ROOT is set.
    data_root: Path = field(default_factory=_resolve_data_root)

    @property
    def is_isolated_data_root(self) -> bool:
        """True when this run is writing somewhere other than the real
        ledger — the CLI prints a banner on this so a test run can never
        be mistaken for a real one at a glance.
        """
        return self.data_root != self.project_root


settings = Settings()
