#!/usr/bin/env python3
"""Stdlib-only startup bisect for Termux SIGSEGV isolation.

Each step runs in a fresh subprocess so one segfault does not kill the probe.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _install_root() -> Path:
    env = os.environ.get("DENG_REJOIN_HOME", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    if here.parent.name == "tools":
        return here.parent.parent
    return Path.home() / ".deng-tool" / "rejoin"


def _signal_label(rc: int) -> str:
    if rc < 0:
        import signal

        sig = -rc
        name = {getattr(signal, n): n for n in dir(signal) if n.startswith("SIG")}.get(sig, str(sig))
        if sig == getattr(signal, "SIGSEGV", 11):
            return "segmentation fault"
        return f"signal {name}"
    if rc == 139:
        return "segmentation fault"
    if rc == 134:
        return "abort"
    return ""


def _run_step(name: str, code: str, *, root: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    env["DENG_REJOIN_HOME"] = str(root)
    env.setdefault("DENG_BOOT_TRACE", "1")
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    sig = _signal_label(proc.returncode)
    print(f"=== {name} ===")
    print(f"exit_code: {proc.returncode}")
    if sig:
        print(f"signal: {sig}")
    if proc.stdout:
        print("stdout:")
        print(proc.stdout.rstrip())
    if proc.stderr:
        print("stderr:")
        print(proc.stderr.rstrip())
    print()


def main() -> int:
    root = _install_root()
    agent = root / "agent"
    entry = agent / "deng_tool_rejoin.py"
    print(f"install_root: {root}")
    print(f"python: {sys.version.split()[0]}")
    print()

    steps: list[tuple[str, str]] = [
        (
            "python_version",
            "import sys; print(sys.version)",
        ),
        (
            "import_deng_tool_rejoin_module",
            "import importlib.util, sys; spec=importlib.util.spec_from_file_location("
            + repr("agent.deng_tool_rejoin")
            + ", "
            + repr(str(entry))
            + "); mod=importlib.util.module_from_spec(spec); "
            "spec.loader.exec_module(mod); print('import_ok')",
        ),
        (
            "version_standalone",
            "import runpy; runpy.run_path("
            + repr(str(agent / "version_standalone.py"))
            + ", run_name='__main__')",
        ),
        (
            "install_verify_standalone",
            "import runpy; runpy.run_path("
            + repr(str(agent / "install_verify_standalone.py"))
            + ", run_name='__main__')",
        ),
        (
            "import_protected_runtime",
            "import sys; sys.path.insert(0, "
            + repr(str(root))
            + "); import importlib; import types; "
            "pkg=types.ModuleType('agent'); pkg.__path__=["
            + repr(str(agent))
            + "]; sys.modules['agent']=pkg; "
            "m=importlib.import_module('agent._protected_runtime'); "
            "m.install(); print('protected_ok')",
        ),
        (
            "import_commands",
            "import sys; sys.path.insert(0, "
            + repr(str(root))
            + "); import importlib; import types; "
            "pkg=types.ModuleType('agent'); pkg.__path__=["
            + repr(str(agent))
            + "]; sys.modules['agent']=pkg; "
            "rt=importlib.import_module('agent._protected_runtime'); rt.install(); "
            "import agent.commands; print('commands_ok')",
        ),
        (
            "import_supervisor",
            "import sys; sys.path.insert(0, "
            + repr(str(root))
            + "); import importlib; import types; "
            "pkg=types.ModuleType('agent'); pkg.__path__=["
            + repr(str(agent))
            + "]; sys.modules['agent']=pkg; "
            "rt=importlib.import_module('agent._protected_runtime'); rt.install(); "
            "import agent.supervisor; print('supervisor_ok')",
        ),
        (
            "import_roblox_presence",
            "import sys; sys.path.insert(0, "
            + repr(str(root))
            + "); import importlib; import types; "
            "pkg=types.ModuleType('agent'); pkg.__path__=["
            + repr(str(agent))
            + "]; sys.modules['agent']=pkg; "
            "rt=importlib.import_module('agent._protected_runtime'); rt.install(); "
            "import agent.roblox_presence; print('presence_ok')",
        ),
        (
            "entrypoint_help",
            "import runpy, sys; sys.argv=['deng-rejoin','help']; "
            "runpy.run_path(" + repr(str(entry)) + ", run_name='__main__')",
        ),
        (
            "entrypoint_plain_menu",
            "import runpy, sys; sys.argv=['deng-rejoin']; "
            "runpy.run_path(" + repr(str(entry)) + ", run_name='__main__')",
        ),
    ]

    for name, code in steps:
        _run_step(name, code, root=root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
