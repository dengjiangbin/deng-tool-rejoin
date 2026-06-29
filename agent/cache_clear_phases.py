"""Two-phase cache clear (probes p-f499f7533a, p-7d483f2f27, p-536c439c42).

Phase 1 — Start prep: mass clear every selected clone via a detached root
script under ``/data/local/tmp`` so Termux never waits on a heavy ``su`` +
``find``/``rm`` tree (inline and python-child paths still SIGSEGV'd).

Phase 2 — dead recovery only: clear cache for one target package at a time,
inline through one locked root shell.
"""

from __future__ import annotations

import time

from . import android


def _settle_before_start_cache_clear() -> None:
    """Brief pause so fork/exec after force-stop prep is less crash-prone."""
    if android.is_termux():
        time.sleep(1.0)


def run_start_mass_cache_clear(
    packages: list[str],
    *,
    root_info: android.RootInfo | None = None,
) -> dict[str, str]:
    """Phase 1: clear all selected packages (detached mass wipe on Termux)."""
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
