"""Two cache-clear types for a crash-proof Start + surgical recovery.

TYPE A — Start prep (``run_start_mass_cache_clear``): clears cache for every
selected clone at once.  Used only while Start is preparing the batch.

TYPE B — Dead recovery (``run_recovery_cache_clear``): clears cache for the
single dead package that is about to be relaunched.  Recovery never mass-closes
Termux or the other clones — only the one package being restored is touched.

Both types share the same proven primitive: one locked root shell per package
using ``find -delete`` (``agent.android.clear_package_cache_for_start``).  That
primitive runs in the multithreaded watchdog during recovery without ever
crashing, so Start reuses it instead of the experimental Python-child /
detached / combined ``rm -rf`` variants that SIGSEGV'd Termux/Python 3.13
(probes p-7dac7cb6c4, p-536c439c42, p-22bfe0518a, p-9d6d6a8cc3, p-70897e1166).
"""

from __future__ import annotations

import time

from . import android


def _settle_before_start_cache_clear() -> None:
    """Brief pause so the force-stop prep burst settles before cache clear."""
    if android.is_termux():
        time.sleep(0.5)


def run_start_mass_cache_clear(
    packages: list[str],
    *,
    root_info: android.RootInfo | None = None,
) -> dict[str, str]:
    """TYPE A: mass cache clear for every selected package (Start prep only)."""
    if not packages:
        return {}
    _settle_before_start_cache_clear()
    return android.clear_packages_cache_mass_batch(packages, root_info=root_info)


def run_recovery_cache_clear(
    package: str,
    *,
    root_info: android.RootInfo | None = None,
) -> dict[str, object]:
    """TYPE B: clear cache for the one dead package before its relaunch."""
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
