"""Layer 3 — hotdata.dev: live query/analytics over campaign performance data.

Real path, verified working (2026-09-11): workspace "marketing hack"
provisioned, instant database "automarketer" created (catalog alias
`automarketer`), table `automarketer.public.post_metrics` created by an
initial load. Each simulated publish appends its own row via
`hotdata databases load --append` (a real write), and `get_metrics()` reads
it back with a real `hotdata query` SQL call — this is the "what's
happening in the data right now" layer, exercised on every pipeline run,
not a one-off import.

Falls back to a clearly-labeled mock only if HOTDATA_ENABLED=false or the
CLI call fails, so the rest of the loop never stalls on this layer.
"""
from __future__ import annotations

import csv
import json
import random
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import HotdataSettings, settings

CATALOG = "automarketer"
TABLE = "post_metrics"


class HotdataClient:
    def __init__(self, cfg: HotdataSettings | None = None):
        self.cfg = cfg or settings.hotdata

    def write_metrics(self, post_id: str, channel: str, metrics: dict[str, Any]) -> None:
        """Append one post's metrics as a real row in hotdata.dev."""
        if not self.cfg.enabled:
            return
        row = {
            "post_id": post_id,
            "channel": channel,
            "impressions": metrics["impressions"],
            "clicks": metrics["clicks"],
            "conversions": metrics["conversions"],
            "ctr": metrics["ctr"],
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(row.keys()))
            writer.writeheader()
            writer.writerow(row)
            path = fh.name
        try:
            subprocess.run(
                ["hotdata", "databases", "load", "--catalog", CATALOG, "--table", TABLE,
                 "--file", path, "--append", "--no-input"],
                capture_output=True, text=True, timeout=30, check=True,
            )
        except Exception as e:  # noqa: BLE001 — best-effort write, never blocks the loop
            print(f"[warn] hotdata write_metrics failed: {e}")
        finally:
            Path(path).unlink(missing_ok=True)

    def get_metrics(self, post_id: str) -> dict[str, Any]:
        if self.cfg.enabled:
            try:
                return self._real_query(post_id)
            except Exception as e:  # noqa: BLE001
                print(f"[warn] hotdata query failed ({e}), falling back to mock")
        return self._mock_metrics(post_id)

    # -- real path (hotdata.dev CLI, SQL over DataFusion/DuckLake) --------
    def _real_query(self, post_id: str) -> dict[str, Any]:
        sql = (
            f"SELECT impressions, clicks, conversions, ctr "
            f"FROM {CATALOG}.public.{TABLE} WHERE post_id = '{post_id}'"
        )
        result = subprocess.run(
            ["hotdata", "query", sql, "-o", "json", "--no-input"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        payload = json.loads(result.stdout)
        rows = payload.get("rows", [])
        if not rows:
            raise RuntimeError(f"no hotdata row for post_id={post_id}")
        row = dict(zip(payload["columns"], rows[0]))
        return {"source": "hotdata.dev (live)", "post_id": post_id, **row}

    # -- mock fallback, clearly labeled --------------------------------------
    def _mock_metrics(self, post_id: str) -> dict[str, Any]:
        impressions = random.randint(400, 4000)
        clicks = int(impressions * random.uniform(0.01, 0.06))
        conversions = int(clicks * random.uniform(0.02, 0.15))
        return {
            "source": "MOCK — hotdata.dev not connected",
            "post_id": post_id,
            "impressions": impressions,
            "clicks": clicks,
            "conversions": conversions,
            "ctr": round(clicks / impressions, 4) if impressions else 0,
        }
