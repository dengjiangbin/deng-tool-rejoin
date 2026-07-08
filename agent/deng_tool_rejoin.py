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

_ENTRY_ROOT = Path(__file__).resolve().parent
_INSTALL_ROOT = _ENTRY_ROOT.parent


def _dispatch_install_safe_version(argv: list[str]) -> int | None:
    """Handle ``version`` before importing protected runtime / agent.commands."""
    if not argv:
        return None
    head = argv[0]
    if head not in ("version", "--version"):
        return None
    version_script = _ENTRY_ROOT / "version_standalone.py"
    if not version_script.is_file():
        return 1
    import runpy

    try:
        runpy.run_path(str(version_script), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    return 0


if __name__ == "__main__":
    _version_rc = _dispatch_install_safe_version(sys.argv[1:])
    if _version_rc is not None:
        raise SystemExit(_version_rc)

if __package__ in (None, ""):
    sys.path.insert(0, str(_INSTALL_ROOT))
    from agent.commands import main
else:
    from .commands import main


if __name__ == "__main__":
    _lime_rc = None
    try:
        if __package__ in (None, ""):
            from agent.lime_cli_dispatch import try_dispatch_lime_argv
        else:
            from .lime_cli_dispatch import try_dispatch_lime_argv

        _lime_rc = try_dispatch_lime_argv(sys.argv[1:])
    except Exception:  # noqa: BLE001
        _lime_rc = None
    if _lime_rc is not None:
        raise SystemExit(_lime_rc)
    raise SystemExit(main())
