#!/usr/bin/env python3
"""Compatibility entry point for route, record, and stats."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from select_model.cli import main  # noqa: E402

if __name__ == "__main__":
    arguments = sys.argv[1:]
    if not arguments or arguments[0] not in {"route", "record", "stats", "profile"}:
        arguments = ["route", *arguments]
    raise SystemExit(main(arguments))
