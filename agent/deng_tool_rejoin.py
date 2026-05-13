#!/usr/bin/env python3
"""DENG Tool: Rejoin CLI entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from agent.commands import main
else:
    from .commands import main


if __name__ == "__main__":
    raise SystemExit(main())
