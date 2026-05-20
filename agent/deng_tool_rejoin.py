#!/usr/bin/env python3
"""DENG Tool: Rejoin CLI entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path

# [DENG_REJOIN_SEGFAULT_FIX] probe_id=p-ea167faf5f; supersedes probe p-9e3f2a8d1c.
# faulthandler with file=<open_fd> + all_threads=True caused SIGSEGV on
# Python 3.13 ARM/Termux in threaded processes (supervisor spawns multiple
# worker threads; faulthandler's all_threads walk races with thread teardown).
# The open file descriptor was also inherited by every su/am subprocess, leaking
# it into root commands.  Removed entirely — Python 3.13 prints native SIGSEGV
# traces to stderr by default without any faulthandler setup.

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from agent.commands import main
else:
    from .commands import main


if __name__ == "__main__":
    raise SystemExit(main())
