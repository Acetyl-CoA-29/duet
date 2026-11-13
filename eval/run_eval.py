#!/usr/bin/env python3
"""Entry point for the duet comparison harness. See eval/README.md."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evallib.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
