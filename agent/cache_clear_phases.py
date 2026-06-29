"""Two-phase cache clear (probes p-f499f7533a, p-7d483f2f27).

Phase 1 — Start prep: mass clear every selected clone in one root shell,
executed inline through :func:`agent.android.run_root_command` (serialized
subprocess lock).  Spawning a nested ``python3 -c`` child here still
SIGSEGV'd Termux after the force-stop burst on first-time cache wipes.

Phase 2 — dead recovery only: clear cache for one target package at a time,
also inline (one root shell, same lock).
"""

from __future__ import annotations

import time

from . import android


def _settle_before_start_cache_clear() -> None:
    """Brief pause so fork/exec after force-stop prep is less crash-prone."""
    if android.is_termux():
        time.sleep(0.75)


def run_start_mass_cache_clear(
    packages: list[str],
    *,
    root_info: android.RootInfo | None = None,
) -> dict[str, str]:
    """Phase 1: clear all selected packages in one mass root shell."""
    if not packages:
        return {}
    _settle_before_start_cache_clear()
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
