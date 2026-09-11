"""Loads .env and exposes typed settings for every layer of the pipeline."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CogneeSettings:
    base_url: str = field(default_factory=lambda: os.environ.get("COGNEE_BASE_URL", "http://localhost:8000"))
    email: str = field(default_factory=lambda: os.environ.get("COGNEE_EMAIL", ""))
    password: str = field(default_factory=lambda: os.environ.get("COGNEE_PASSWORD", ""))
    dataset: str = field(default_factory=lambda: os.environ.get("COGNEE_DATASET", "automarketer"))
    llm_api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY", ""))


@dataclass(frozen=True)
class HydraDBSettings:
    http_url: str = field(default_factory=lambda: os.environ.get("HYDRADB_HTTP_URL", "http://127.0.0.1:8443"))
    auth_token_file: str = field(default_factory=lambda: os.environ.get("HYDRADB_AUTH_TOKEN_FILE", "hydradb-data/auth-token"))
    namespace: str = field(default_factory=lambda: os.environ.get("HYDRADB_NAMESPACE", "default"))
    graph: str = field(default_factory=lambda: os.environ.get("HYDRADB_GRAPH", "default"))
    cell_id: str = field(default_factory=lambda: os.environ.get("HYDRADB_CELL_ID", "cell-0"))

    @property
    def auth_token(self) -> str:
        # Snyk (static scan) flagged unsanitized env input flowing into a path
        # join as a potential path-traversal vector. HYDRADB_AUTH_TOKEN_FILE is
        # operator-controlled local config, not untrusted input, but resolving
        # and confining it to the project root closes the gap for free.
        path = (PROJECT_ROOT / self.auth_token_file).resolve()
        if not path.is_relative_to(PROJECT_ROOT.resolve()):
            raise ValueError(
                f"HYDRADB_AUTH_TOKEN_FILE must resolve inside the project root, got {path}"
            )
        return path.read_text().strip()


@dataclass(frozen=True)
class RocketRideSettings:
    uri: str = field(default_factory=lambda: os.environ.get("ROCKETRIDE_URI", "https://staging.rocketride.ai"))
    api_key: str = field(default_factory=lambda: os.environ.get("ROCKETRIDE_API_KEY", ""))


@dataclass(frozen=True)
class HotdataSettings:
    enabled: bool = field(default_factory=lambda: _bool("HOTDATA_ENABLED", False))
    api_key: str = field(default_factory=lambda: os.environ.get("HOTDATA_API_KEY", ""))
    workspace_id: str = field(default_factory=lambda: os.environ.get("HOTDATA_WORKSPACE_ID", ""))


@dataclass(frozen=True)
class Settings:
    cognee: CogneeSettings = field(default_factory=CogneeSettings)
    hydradb: HydraDBSettings = field(default_factory=HydraDBSettings)
    rocketride: RocketRideSettings = field(default_factory=RocketRideSettings)
    hotdata: HotdataSettings = field(default_factory=HotdataSettings)
    project_root: Path = PROJECT_ROOT


settings = Settings()
