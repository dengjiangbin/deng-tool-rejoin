#!/usr/bin/env python3
"""DENG Tool: Rejoin CLI entrypoint.

Stdlib-only until argv dispatch completes. Runtime mode (source vs protected)
is chosen before importing ``agent.commands`` so Termux/Android never boots
the marshalled protected import hook during normal startup.
"""

from __future__ import annotations

import os
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
# Popen — runs.
try:  # pragma: no cover - exercised on-device, no-op on Windows
    if hasattr(_subprocess, "_USE_VFORK"):
        _subprocess._USE_VFORK = False  # type: ignore[attr-defined]
    if hasattr(_subprocess, "_USE_POSIX_SPAWN"):
        _subprocess._USE_POSIX_SPAWN = False  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_ENTRY_ROOT = Path(__file__).resolve().parent
_INSTALL_ROOT = _ENTRY_ROOT.parent
_BOOT_TRACE = os.environ.get("DENG_BOOT_TRACE", "").strip().lower() in {"1", "true", "yes", "on"}
_BOOT_STEP = 0
_RUNTIME_MODES = frozenset({"auto", "source", "protected"})


def _boot_trace(label: str) -> None:
    """Emit ordered stderr markers when ``DENG_BOOT_TRACE=1`` (flush every line)."""
    if not _BOOT_TRACE:
        return
    global _BOOT_STEP
    _BOOT_STEP += 1
    sys.stderr.write(f"BOOT {_BOOT_STEP:03d} {label}\n")
    sys.stderr.flush()


def _ensure_install_root_on_path() -> None:
    root = str(_INSTALL_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _detect_termux() -> bool:
    """Best-effort Termux/Android detection without importing agent modules."""
    prefix = os.environ.get("PREFIX", "").replace("\\", "/")
    if "/com.termux/" in prefix:
        return True
    if os.environ.get("TERMUX_VERSION", "").strip():
        return True
    if Path("/data/data/com.termux/files").is_dir():
        return True
    if sys.platform == "linux" and Path("/system/bin/app_process").exists():
        if Path("/data/data/com.termux/files/usr/bin/python3").exists():
            return True
    return False


def _resolve_runtime_mode() -> str:
    """Return ``source`` or ``protected`` after applying auto detection."""
    requested = os.environ.get("DENG_RUNTIME_MODE", "auto").strip().lower() or "auto"
    if requested not in _RUNTIME_MODES:
        requested = "auto"
    if requested == "auto":
        return "source" if _detect_termux() else "protected"
    return requested


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


def _dispatch_install_safe_help(argv: list[str]) -> int | None:
    if not argv:
        return None
    if argv[0] not in ("help", "--help", "-h"):
        return None
    sys.stdout.write(
        "DENG Tool: Rejoin\n"
        "Usage: deng-rejoin [command]\n"
        "  (no command)  Open menu\n"
        "  version       Print installed artifact SHA\n"
        "  help          Show this message\n"
    )
    sys.stdout.flush()
    return 0


def _ensure_agent_package_stub() -> None:
    """Register a minimal ``agent`` package before protected runtime install."""
    if "agent" in sys.modules:
        return
    import types

    pkg = types.ModuleType("agent")
    pkg.__path__ = [str(_ENTRY_ROOT)]  # type: ignore[attr-defined]
    pkg.__package__ = "agent"
    version = "1.0.0"
    build_info = _INSTALL_ROOT / "BUILD-INFO.json"
    if build_info.is_file():
        try:
            import json

            version = str(json.loads(build_info.read_text(encoding="utf-8")).get("version") or version)
        except Exception:  # noqa: BLE001
            pass
    pkg.__version__ = version.lstrip("v")  # type: ignore[attr-defined]
    sys.modules["agent"] = pkg


def _install_protected_runtime() -> bool:
    runtime_path = _ENTRY_ROOT / "_protected_runtime.py"
    bundle_path = _ENTRY_ROOT / ".deng_runtime.bin"
    if not runtime_path.is_file() or not bundle_path.is_file():
        _boot_trace("protected runtime absent; using plain source modules")
        return False
    _ensure_agent_package_stub()
    import importlib

    _boot_trace("before import agent._protected_runtime")
    runtime = importlib.import_module("agent._protected_runtime")
    _boot_trace("after import agent._protected_runtime")
    install = getattr(runtime, "install", None)
    if callable(install):
        _boot_trace("before protected runtime install()")
        install()
        _boot_trace("after protected runtime install()")
    return True


def _bootstrap_runtime(mode: str) -> str:
    """Prepare import path for the selected runtime mode."""
    _ensure_install_root_on_path()
    if mode == "source":
        _boot_trace("using source runtime, protected runtime skipped")
        return mode
    _boot_trace("using protected runtime")
    if not _install_protected_runtime():
        _boot_trace("protected runtime unavailable; falling back to source runtime")
        return "source"
    return "protected"


def _import_commands_main():
    _boot_trace("before import agent.commands")
    if __package__ in (None, ""):
        from agent.commands import main as _main
    else:
        from .commands import main as _main
    _boot_trace("after import agent.commands")
    return _main


def _try_dispatch_lime_argv(argv: list[str]) -> int | None:
    _boot_trace("before import agent.lime_cli_dispatch")
    try:
        if __package__ in (None, ""):
            from agent.lime_cli_dispatch import try_dispatch_lime_argv
        else:
            from .lime_cli_dispatch import try_dispatch_lime_argv
    except Exception:  # noqa: BLE001
        _boot_trace("lime_cli_dispatch import failed")
        return None
    _boot_trace("after import agent.lime_cli_dispatch")
    try:
        return try_dispatch_lime_argv(argv)
    except Exception:  # noqa: BLE001
        return None


def _run_cli(argv: list[str]) -> int:
    _boot_trace("entrypoint entered")
    _boot_trace(f"parsed argv={argv!r}")

    version_rc = _dispatch_install_safe_version(argv)
    if version_rc is not None:
        return version_rc

    help_rc = _dispatch_install_safe_help(argv)
    if help_rc is not None:
        return help_rc

    requested_mode = os.environ.get("DENG_RUNTIME_MODE", "auto").strip().lower() or "auto"
    if requested_mode not in _RUNTIME_MODES:
        requested_mode = "auto"
    _boot_trace(f"runtime mode {requested_mode}")
    if _detect_termux():
        _boot_trace("termux detected")

    mode = _resolve_runtime_mode()
    active_mode = _bootstrap_runtime(mode)

    main = _import_commands_main()

    lime_rc = _try_dispatch_lime_argv(argv)
    if lime_rc is not None:
        return lime_rc

    _boot_trace("before agent.commands.main()")
    rc = main(argv)
    _boot_trace(f"after agent.commands.main() rc={rc}")
    if active_mode == "protected" and _detect_termux():
        _boot_trace("warning: protected runtime used on termux")
    return rc


if __name__ == "__main__":
    raise SystemExit(_run_cli(sys.argv[1:]))
