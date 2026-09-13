#!/usr/bin/env python3
"""Deprecated entry point — kept so old muscle memory still works. Prefer:

    ./.venv/bin/automarketer run --whitepaper "..." --channel x

(after `./.venv/bin/pip install -e .` — see README.md). This file just
forwards to the same code.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from automarketer.cli import main as _cli_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(_cli_main(["run"] + sys.argv[1:]))
