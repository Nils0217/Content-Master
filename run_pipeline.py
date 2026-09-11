#!/usr/bin/env python3
"""Entry point. Usage:

  ./.venv/bin/python run_pipeline.py --whitepaper "Marketing hack white paper.pdf" --channel x
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from automarketer.pipeline import main  # noqa: E402

if __name__ == "__main__":
    main()
