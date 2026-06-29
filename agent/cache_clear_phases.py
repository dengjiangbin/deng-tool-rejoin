"""Two-phase cache clear (probe p-f499f7533a).

Phase 1 — Start prep: mass clear every selected clone in one root shell,
optionally inside one short-lived child so Termux Start never SIGSEGVs.

Phase 2 — dead recovery only: clear cache for the one target package at a
time, also isolated on Termux/Android.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from . import android


def should_isolate_cache_clear() -> bool:
    """Run cache-clear root work in a child on Termux/Android."""
    override = (os.environ.get("DENG_START_CACHE_INLINE") or "").strip().lower()
    if override in ("1", "true", "yes", "on"):
        return False
    if "unittest" in sys.modules and any(
        "unittest" in a or "pytest" in a or "_test_runner" in a for a in sys.argv[:2]
    ):
        return False
    if os.environ.get("TERMUX_VERSION"):
        return True
    if os.environ.get("ANDROID_ROOT") or os.environ.get("ANDROID_DATA"):
        return True
    return False


def _agent_parent() -> str:
    try:
        from pathlib import Path

        return str(Path(__file__).resolve().parent.parent)
    except Exception:  # noqa: BLE001
        return os.path.expanduser("~/.deng-tool/rejoin")


def _child_env(agent_parent: str) -> dict[str, str]:
    env = dict(os.environ)
    prev_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = agent_parent + (os.pathsep + prev_pp if prev_pp else "")
    return env


def _run_python_child(code: str, payload: object, *, timeout: int) -> tuple[int, str]:
    import subprocess as sp

    agent_parent = _agent_parent()
    full_code = (
        "import json, os, sys\n"
        f"sys.path.insert(0, {agent_parent!r})\n"
        "_home = os.environ.get('DENG_REJOIN_HOME')\n"
        "if _home and _home not in sys.path:\n"
        "    sys.path.insert(0, _home)\n"
        + code
    )
    try:
        proc = sp.Popen(
            [sys.executable, "-c", full_code],
            stdin=sp.PIPE,
            stdout=sp.PIPE,
            stderr=sp.DEVNULL,
            env=_child_env(agent_parent),
        )
        stdout, _stderr = proc.communicate(
            input=json.dumps(payload).encode("utf-8"),
            timeout=timeout,
        )
    except sp.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        return -1, ""
    except OSError:
        return -1, ""
    rc = proc.returncode if proc.returncode is not None else -1
    return rc, (stdout or b"").decode("utf-8", errors="replace")


def run_start_mass_cache_clear(packages: list[str]) -> dict[str, str]:
    """Phase 1: clear all selected packages in one mass root shell."""
    if not packages:
        return {}
    if should_isolate_cache_clear():
        rc, stdout = _run_python_child(
            "from agent import android\n"
            "pkgs = json.loads(sys.stdin.read())\n"
            "try:\n"
            "    out = android.clear_packages_cache_mass_batch(pkgs)\n"
            "except Exception:\n"
            "    out = {p: 'Failed' for p in pkgs}\n"
            "sys.stdout.write(json.dumps(out))\n",
            packages,
            timeout=max(120, len(packages) * 30),
        )
        if rc < 0 or rc != 0:
            return {pkg: "Failed" for pkg in packages}
        try:
            data = json.loads(stdout or "{}")
        except json.JSONDecodeError:
            return {pkg: "Failed" for pkg in packages}
        if not isinstance(data, dict):
            return {pkg: "Failed" for pkg in packages}
        return {str(k): str(v) for k, v in data.items()}
    return android.clear_packages_cache_mass_batch(packages)


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
    if should_isolate_cache_clear():
        rc, stdout = _run_python_child(
            "from agent import android\n"
            "pkg = json.loads(sys.stdin.read())\n"
            "try:\n"
            "    out = android.clear_package_cache_recovery(pkg)\n"
            "except Exception as exc:\n"
            "    out = {\n"
            "        'success': False, 'skipped': False, 'skipped_reason': '',\n"
            "        'method': 'recovery_single', 'error': str(exc)[:120],\n"
            "    }\n"
            "sys.stdout.write(json.dumps(out))\n",
            pkg,
            timeout=60,
        )
        if rc < 0 or rc != 0:
            return {
                "success": False,
                "skipped": False,
                "skipped_reason": "",
                "method": "recovery_single",
                "error": "child_failed",
            }
        try:
            data = json.loads(stdout or "{}")
        except json.JSONDecodeError:
            return {
                "success": False,
                "skipped": False,
                "skipped_reason": "",
                "method": "recovery_single",
                "error": "invalid_child_json",
            }
        if not isinstance(data, dict):
            return {
                "success": False,
                "skipped": False,
                "skipped_reason": "",
                "method": "recovery_single",
                "error": "invalid_child_payload",
            }
        return data
    return android.clear_package_cache_recovery(pkg, root_info=root_info)
