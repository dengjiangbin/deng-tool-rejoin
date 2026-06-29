#!/usr/bin/env python3
"""DENG Tool: Rejoin CLI entrypoint."""

from __future__ import annotations

import subprocess as _subprocess
import sys
from pathlib import Path

# [DENG_REJOIN_SEGFAULT_FIX] probe_id=p-ea167faf5f; supersedes probe p-9e3f2a8d1c.
# faulthandler with file=<open_fd> + all_threads=True caused SIGSEGV on
# Python 3.13 ARM/Termux in threaded processes (supervisor spawns multiple
# worker threads; faulthandler's all_threads walk races with thread teardown).
# The open file descriptor was also inherited by every su/am subprocess, leaking
# it into root commands.  Removed entirely — Python 3.13 prints native SIGSEGV
# traces to stderr by default without any faulthandler setup.

# [DENG_REJOIN_SEGFAULT_FIX] probe_id=p-3daeae4cbd.  Force plain fork() (not
# vfork/posix_spawn) for every subprocess spawn BEFORE any agent module — or any
# Popen — runs.  Start reached 'layout_done' then SIGSEGV'd on the next Popen
# (the monitor-bridge interpreter spawn), which falls back to fork_exec+vfork;
# vfork shares the parent address space and corrupts it under Termux/bionic with
# live watchdog + logcat threads.  Set at the entrypoint so both the parent and
# the spawned monitor worker (which re-enters here) inherit the safe path.
try:  # pragma: no cover - exercised on-device, no-op on Windows
    if hasattr(_subprocess, "_USE_VFORK"):
        _subprocess._USE_VFORK = False  # type: ignore[attr-defined]
    if hasattr(_subprocess, "_USE_POSIX_SPAWN"):
        _subprocess._USE_POSIX_SPAWN = False  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from agent.commands import main
else:
    from .commands import main


if __name__ == "__main__":
    raise SystemExit(main())
