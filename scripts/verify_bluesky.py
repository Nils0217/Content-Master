#!/usr/bin/env python3
"""Deprecated entry point — kept so old muscle memory still works. Prefer:

    ./.venv/bin/automarketer bluesky verify --search "..." --limit 3

(after `./.venv/bin/pip install -e .` — see README.md). This file just
forwards to the same code.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from automarketer.cli import main as _cli_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(_cli_main(["bluesky", "verify"] + sys.argv[1:]))
