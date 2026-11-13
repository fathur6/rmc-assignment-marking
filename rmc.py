#!/usr/bin/env python3
"""Entry point: run the module with its bundled virtual environment.

    .venv/bin/python rmc.py mark path/to/manuscript.pdf --out ./output
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
