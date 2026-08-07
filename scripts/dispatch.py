#!/usr/bin/env python3
"""Compatibility entry point for safe Responses API dispatch."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from select_model.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["dispatch", *sys.argv[1:]]))
