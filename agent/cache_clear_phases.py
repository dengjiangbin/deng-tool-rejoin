"""Two-phase cache clear (probes p-f499f7533a, p-536c439c42, p-22bfe0518a, p-9d6d6a8cc3).

Phase 1 — Start prep: mass clear every selected clone. On Termux the parent
never forks ``su`` — a short-lived Python child writes a shell script and
launches it via a tiny ``nohup sh script &`` wrapper.

Phase 2 — dead recovery only: clear cache for one target package at a time,
inline through one locked root shell.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from . import android


def _settle_before_start_cache_clear() -> None:
    """Brief pause so fork/exec after force-stop prep is less crash-prone."""
    if android.is_termux():
        time.sleep(1.0)


def _background_cache_settle_after_dispatch() -> None:
    """Pure-Python pause so detached wipe can progress before prep continues."""
    if android.is_termux():
        time.sleep(2.5)


def _run_start_mass_cache_clear_termux_isolated(
    packages: list[str],
    *,
    root_info: android.RootInfo | None = None,
) -> dict[str, str]:
    """Run Termux mass cache clear in a child — parent survives SIGSEGV (p-9d6d6a8cc3)."""
    from pathlib import Path

    try:
        agent_parent = str(Path(__file__).resolve().parent.parent)
    except Exception:  # noqa: BLE001
        agent_parent = os.path.expanduser("~/.deng-tool/rejoin")

    root_tool = "su"
    if root_info and root_info.tool:
        root_tool = str(root_info.tool)

    code = (
        "import json, os, sys\n"
        f"sys.path.insert(0, {agent_parent!r})\n"
        "_home = os.environ.get('DENG_REJOIN_HOME')\n"
        "if _home and _home not in sys.path:\n"
        "    sys.path.insert(0, _home)\n"
        "from agent import android\n"
        "p = json.loads(sys.stdin.read())\n"
        "pkgs = p['packages']\n"
        "tool = p.get('root_tool') or 'su'\n"
        "try:\n"
        "    results = android.clear_packages_cache_mass_batch_termux(pkgs, root_tool=tool)\n"
        "except Exception:\n"
        "    results = {pkg: 'Failed' for pkg in pkgs}\n"
        "sys.stdout.write(json.dumps({'results': results}))\n"
    )
    env = dict(os.environ)
    prev_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = agent_parent + (os.pathsep + prev_pp if prev_pp else "")

    payload = {"packages": packages, "root_tool": root_tool}
    dispatched = {pkg: "Dispatched" for pkg in packages}

    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            stdout, _stderr = proc.communicate(
                input=json.dumps(payload).encode("utf-8"),
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            return dispatched
    except OSError:
        return {pkg: "Failed" for pkg in packages}

    if proc.returncode < 0:
        return dispatched

    if proc.returncode != 0:
        return {pkg: "Failed" for pkg in packages}

    try:
        data = json.loads((stdout or b"").decode("utf-8", errors="replace") or "{}")
        results = data.get("results") or {}
        if isinstance(results, dict) and results:
            return {str(k): str(v) for k, v in results.items()}
    except json.JSONDecodeError:
        pass
    return dispatched


def run_start_mass_cache_clear(
    packages: list[str],
    *,
    root_info: android.RootInfo | None = None,
) -> dict[str, str]:
    """Phase 1: isolated child + script file on Termux, inline elsewhere."""
    if not packages:
        return {}
    _settle_before_start_cache_clear()
    if android.is_termux():
        results = _run_start_mass_cache_clear_termux_isolated(
            packages,
            root_info=root_info,
        )
        if any(v == "Dispatched" for v in results.values()):
            _background_cache_settle_after_dispatch()
        return results
    return android.clear_packages_cache_mass_batch(packages, root_info=root_info)


def run_recovery_cache_clear(
    package: str,
    *,
    root_info: android.RootInfo | None = None,
) -> dict[str, object]:
    """Phase 2: clear cache for one dead package before relaunch."""
    pkg = str(package or "").strip()
    if not pkg:
        return {
            "success": False,
            "skipped": True,
            "skipped_reason": "invalid_package",
            "method": "recovery_single",
            "error": "",
        }
    return android.clear_package_cache_recovery(pkg, root_info=root_info)
