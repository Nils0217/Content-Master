#!/usr/bin/env python3
"""Deprecated entry point — kept so old muscle memory still works. Prefer:

    ./.venv/bin/contentmaster connect bluesky --search "..." --limit 3

(after `./.venv/bin/pip install -e .` — see README.md). Bluesky is one
Platform adapter among several now (see src/contentmaster/platforms/);
`contentmaster connect <platform>` works the same way for any of them.
This file just forwards to the same code.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from contentmaster.cli import main as _cli_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(_cli_main(["connect", "bluesky"] + sys.argv[1:]))
