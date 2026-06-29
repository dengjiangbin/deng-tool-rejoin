"""Two-phase cache clear (probes p-f499f7533a, p-536c439c42, p-22bfe0518a).

Phase 1 — Start prep: mass clear every selected clone via a single detached
``su`` shell on Termux. Termux must never call ``run_root_command`` or poll
during Start — that still SIGSEGVs after the force-stop burst.

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


def _background_cache_settle_after_dispatch() -> None:
    """Pure-Python pause so detached wipe can progress before prep continues."""
    if android.is_termux():
        time.sleep(2.5)


def run_start_mass_cache_clear(
    packages: list[str],
    *,
    root_info: android.RootInfo | None = None,
) -> dict[str, str]:
    """Phase 1: fire-and-forget mass clear on Termux, inline elsewhere."""
    if not packages:
        return {}
    _settle_before_start_cache_clear()
    results = android.clear_packages_cache_mass_batch(packages, root_info=root_info)
    if android.is_termux() and any(v == "Dispatched" for v in results.values()):
        _background_cache_settle_after_dispatch()
    return results


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
