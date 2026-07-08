#!/usr/bin/env python3
"""Stdlib-only startup bisect for Termux SIGSEGV isolation.

Each step runs in a fresh subprocess so one segfault does not kill the probe.
Tests protected (marshal) and source (.py) import paths separately.
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


def _agent_pkg_setup(root: str, agent: str) -> str:
    return (
        "import sys; sys.path.insert(0, "
        + repr(root)
        + "); import types; "
        "pkg=types.ModuleType('agent'); pkg.__path__=["
        + repr(agent)
        + "]; sys.modules['agent']=pkg; "
    )


def _protected_install_snippet() -> str:
    return (
        "import importlib; "
        "rt=importlib.import_module('agent._protected_runtime'); rt.install(); "
    )


def _source_import_snippet(module: str) -> str:
    return f"import importlib; importlib.import_module({module!r}); print({module.split('.')[-1]!r} + '_ok')"


def _run_step(name: str, code: str, *, root: Path, extra_env: dict[str, str] | None = None) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    env["DENG_REJOIN_HOME"] = str(root)
    env.setdefault("DENG_BOOT_TRACE", "1")
    if extra_env:
        env.update(extra_env)
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
        if "protected" in name:
            print("note: protected marshal imports are unsafe on Termux/Android")
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
    root_s = str(root)
    agent_s = str(agent)
    pkg = _agent_pkg_setup(root_s, agent_s)
    protected = _protected_install_snippet()

    print(f"install_root: {root}")
    print(f"python: {sys.version}")
    print()

    steps: list[tuple[str, str, dict[str, str] | None]] = [
        ("python_version", "import sys; print(sys.version)", None),
        (
            "import_deng_tool_rejoin_module",
            "import importlib.util; spec=importlib.util.spec_from_file_location("
            + repr("agent.deng_tool_rejoin")
            + ", "
            + repr(str(entry))
            + "); mod=importlib.util.module_from_spec(spec); "
            "spec.loader.exec_module(mod); print('import_ok')",
            None,
        ),
        (
            "protected_import_runtime_only",
            pkg + protected + "print('protected_ok')",
            {"DENG_RUNTIME_MODE": "protected"},
        ),
        (
            "protected_import_commands_unsafe",
            pkg + protected + _source_import_snippet("agent.commands"),
            {"DENG_RUNTIME_MODE": "protected"},
        ),
        (
            "source_import_commands",
            pkg + _source_import_snippet("agent.commands"),
            {"DENG_RUNTIME_MODE": "source"},
        ),
        (
            "source_import_supervisor",
            pkg + _source_import_snippet("agent.supervisor"),
            {"DENG_RUNTIME_MODE": "source"},
        ),
        (
            "source_import_roblox_presence",
            pkg + _source_import_snippet("agent.roblox_presence"),
            {"DENG_RUNTIME_MODE": "source"},
        ),
        (
            "entrypoint_plain_menu_auto",
            "import runpy, sys; sys.argv=['deng-rejoin']; "
            "runpy.run_path(" + repr(str(entry)) + ", run_name='__main__')",
            {"DENG_RUNTIME_MODE": "auto"},
        ),
        (
            "entrypoint_plain_menu_source",
            "import runpy, sys; sys.argv=['deng-rejoin']; "
            "runpy.run_path(" + repr(str(entry)) + ", run_name='__main__')",
            {"DENG_RUNTIME_MODE": "source"},
        ),
        (
            "entrypoint_plain_menu_protected_unsafe",
            "import runpy, sys; sys.argv=['deng-rejoin']; "
            "runpy.run_path(" + repr(str(entry)) + ", run_name='__main__')",
            {"DENG_RUNTIME_MODE": "protected"},
        ),
    ]

    for name, code, extra_env in steps:
        _run_step(name, code, root=root, extra_env=extra_env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
