"""Keeps warehouse/local.duckdb current with the ledgers it is built from.

2026-10-30: performance_history is a table, so it only changes on `dbt run`,
and nothing ran it. The analysis was comparing posts against 4 rows from
September while the ledgers held 26. An old comparison looks exactly like a
real one, so a failed update stops the analysis instead of carrying on.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import audit
from .config import settings

REFRESH_TIMEOUT_SECONDS = 120


class WarehouseFailed(RuntimeError):
    """History could not be brought up to date or read. Nothing was analysed."""


def failed(reason: str) -> WarehouseFailed:
    """The exception for a failure, recorded so `contentmaster errors` counts it."""
    audit.log_event("warehouse", "failed", error_type="WarehouseFailed", reason=reason)
    return WarehouseFailed(reason)


def run_dbt(project_dir: Path, dbt: Path | None = None) -> None:
    """`dbt run`, from inside the project: its models read ../plays and the like
    relative to where it runs. Not the interpreter's resolved path: .venv/bin
    is where dbt lives, and the resolved one is Homebrew's."""
    dbt = dbt or Path(sys.executable).parent / "dbt"
    try:
        done = subprocess.run(
            [str(dbt), "--no-send-anonymous-usage-stats", "run", "--no-use-colors", "--no-version-check"],
            cwd=project_dir, env={**os.environ, "DBT_PROFILES_DIR": str(project_dir)},
            capture_output=True, text=True, timeout=REFRESH_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        raise failed(f"dbt run took longer than {REFRESH_TIMEOUT_SECONDS} seconds") from None
    except OSError as exc:
        raise failed(f"dbt could not start: {exc}") from None
    if done.returncode != 0:
        raise failed(f"dbt run exited {done.returncode}: {(done.stdout + done.stderr).strip()[-500:]}")


def refresh() -> None:
    """Before each analysis, not each review: a long review waits minutes on
    the models, and a decision made meanwhile adds history. About two seconds.
    Never from an isolated data root: dbt reads the real ledgers."""
    if not settings.is_isolated_data_root:
        run_dbt(settings.project_root / "warehouse")
