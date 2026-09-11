"""Layer 1 — Cognee.ai: turns the raw whitepaper into a structured knowledge graph.

Talks to a self-hosted Cognee instance (docker run cognee/cognee:main) over its
REST API. Requires LLM_API_KEY to be configured on the *container* (see
scripts/configure_cognee_llm.sh) before `cognify()` / `add()` will do real
extraction work — without it Cognee returns LLMAPIKeyNotSetError.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from .config import CogneeSettings, settings


class CogneeClient:
    def __init__(self, cfg: CogneeSettings | None = None):
        self.cfg = cfg or settings.cognee
        self._token: str | None = None

    # -- auth -----------------------------------------------------------
    def _login(self) -> str:
        if self._token:
            return self._token
        resp = requests.post(
            f"{self.cfg.base_url}/api/v1/auth/login",
            data={"username": self.cfg.email, "password": self.cfg.password},
            timeout=30,
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._login()}"}

    # -- ECL pipeline: Extract, Cognify, Load ---------------------------
    def add_document(self, file_path: str | Path, labels: str | None = None) -> dict[str, Any]:
        """Extract: upload a raw file (e.g. the whitepaper PDF) into a dataset."""
        file_path = Path(file_path)
        with file_path.open("rb") as fh:
            files = {"data": (file_path.name, fh, "application/octet-stream")}
            data = {"datasetName": self.cfg.dataset, "run_in_background": "false"}
            if labels:
                data["labels"] = labels
            resp = requests.post(
                f"{self.cfg.base_url}/api/v1/add",
                headers=self._headers(),
                files=files,
                data=data,
                timeout=120,
            )
        resp.raise_for_status()
        return resp.json()

    def add_raw_texts(self, texts: list[str], labels: str | None = None) -> dict[str, Any]:
        """Extract: ingest raw text strings (no file) into the dataset — same
        /api/v1/add endpoint as add_document, via its `raw_data` field.
        Used by the Bluesky integration to feed fetched posts into the same
        ingestion pipeline the whitepaper PDF goes through.
        """
        data: dict[str, Any] = {"datasetName": self.cfg.dataset, "run_in_background": "false", "raw_data": texts}
        if labels:
            # Cognee requires one label per item (comma-separated), not one shared label.
            data["labels"] = ",".join([labels] * len(texts))
        resp = requests.post(
            f"{self.cfg.base_url}/api/v1/add",
            headers=self._headers(),
            files={},  # multipart/form-data with no file parts — raw_data only
            data=data,
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()

    def cognify(self, run_in_background: bool = False) -> dict[str, Any]:
        """Cognify: build the knowledge graph (entities + relationships) for the dataset."""
        resp = requests.post(
            f"{self.cfg.base_url}/api/v1/cognify",
            headers=self._headers(),
            json={"datasets": [self.cfg.dataset], "runInBackground": run_in_background},
            timeout=600,
        )
        resp.raise_for_status()
        return resp.json()

    # -- retrieval --------------------------------------------------------
    def search(self, query: str, search_type: str = "GRAPH_COMPLETION") -> dict[str, Any]:
        resp = requests.post(
            f"{self.cfg.base_url}/api/v1/search",
            headers=self._headers(),
            json={"query": query, "searchType": search_type, "datasets": [self.cfg.dataset]},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()

    def remember(self, text: str, labels: str = "gtm-memory") -> dict[str, Any]:
        """Turn one interaction (e.g. a campaign result) into a durable memory unit."""
        resp = requests.post(
            f"{self.cfg.base_url}/api/v1/remember",
            headers=self._headers(),
            files={},
            data={"raw_data": [text], "labels": labels},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    def recall(self, query: str) -> dict[str, Any]:
        resp = requests.post(
            f"{self.cfg.base_url}/api/v1/recall",
            headers=self._headers(),
            json={"query": query},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    def graph_summary(self) -> dict[str, Any]:
        resp = requests.get(
            f"{self.cfg.base_url}/api/v1/datasets/graph-summary",
            headers=self._headers(),
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()
