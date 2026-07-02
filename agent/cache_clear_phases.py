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

import threading
import time
from typing import Any

from . import android

# Hard ceiling for a single recovery cache clear.  The stage must always
# advance to relaunch — a wrong/hung root shell can never freeze recovery.
RECOVERY_CACHE_CLEAR_DEADLINE_S = 25.0

# Start prep (TYPE A): total wall-clock budget for the whole batch.  Launch
# scheduling is anchored at clear-cache *start* and must never wait for this
# phase to finish (probe p-a5e6f62d28).
START_CACHE_CLEAR_DEADLINE_S = 5.0
START_CACHE_CLEAR_PER_PACKAGE_TIMEOUT_S = 3


def _settle_before_start_cache_clear() -> None:
    """No artificial pause — cache clear starts immediately after prep force-stop."""
    return


def run_start_mass_cache_clear(
    packages: list[str],
    *,
    root_info: android.RootInfo | None = None,
    per_package_timeout_s: int = START_CACHE_CLEAR_PER_PACKAGE_TIMEOUT_S,
) -> dict[str, str]:
    """TYPE A: mass cache clear for every selected package (Start prep only)."""
    if not packages:
        return {}
    _settle_before_start_cache_clear()
    return android.clear_packages_cache_mass_batch(
        packages,
        root_info=root_info,
        per_package_timeout_s=per_package_timeout_s,
    )


def run_start_mass_cache_clear_bounded(
    packages: list[str],
    *,
    root_info: android.RootInfo | None = None,
    deadline_s: float = START_CACHE_CLEAR_DEADLINE_S,
    checker_pointer: Any | None = None,
) -> dict[str, object]:
    """TYPE A with a hard total deadline — never blocks launch scheduling.

    Whether this times out, throws, or partially clears, the caller must
    continue into the launch scheduler immediately after the deadline.
    """
    started_at = time.time()
    if checker_pointer is not None:
        try:
            checker_pointer.begin_cache_clear(command_kind="start_mass_su_find_delete")
        except Exception:  # noqa: BLE001
            pass

    def _finish(
        results: dict[str, str],
        *,
        status: str,
        timed_out: bool,
        exit_code: int,
        error: str,
    ) -> dict[str, object]:
        finished_at = time.time()
        duration_ms = round((finished_at - started_at) * 1000.0, 1)
        out: dict[str, object] = {
            "results": results,
            "cache_clear_started_at": started_at,
            "cache_clear_finished_at": finished_at,
            "cache_clear_duration_ms": duration_ms,
            "cache_clear_timeout": timed_out,
            "cache_clear_status": status,
            "cache_clear_timed_out": timed_out,
            "cache_clear_exit_code": exit_code,
            "cache_clear_error": error,
            "cache_clear_command_kind": "start_mass_su_find_delete",
        }
        if checker_pointer is not None:
            try:
                checker_pointer.record_cache_clear_result(
                    status=status,
                    exit_code=exit_code,
                    timed_out=timed_out,
                    error=error,
                    command_kind="start_mass_su_find_delete",
                )
            except Exception:  # noqa: BLE001
                pass
        return out

    if not packages:
        return _finish({}, status="skipped_empty", timed_out=False, exit_code=0, error="")

    holder: dict[str, Any] = {}

    def _work() -> None:
        try:
            holder["results"] = run_start_mass_cache_clear(
                packages,
                root_info=root_info,
                per_package_timeout_s=START_CACHE_CLEAR_PER_PACKAGE_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001
            holder["error"] = str(exc)[:200]

    worker = threading.Thread(
        target=_work, name="start-mass-cache-clear", daemon=True
    )
    worker.start()
    worker.join(max(0.5, float(deadline_s)))

    if worker.is_alive():
        try:
            from . import start_lifecycle as _start_lifecycle

            _start_lifecycle.request_abort_start_cache_clear()
        except Exception:  # noqa: BLE001
            pass
        partial = dict(holder.get("results") or {})
        for pkg in packages:
            partial.setdefault(str(pkg), "TimedOut")
        return _finish(
            partial,
            status="timeout_continue_launch",
            timed_out=True,
            exit_code=124,
            error="start_cache_clear_deadline_exceeded",
        )

    if "error" in holder:
        failed = {str(pkg): "Failed" for pkg in packages}
        return _finish(
            failed,
            status="failed_continue_launch",
            timed_out=False,
            exit_code=1,
            error=holder["error"],
        )

    results = dict(holder.get("results") or {})
    return _finish(
        results,
        status="success",
        timed_out=False,
        exit_code=0,
        error="",
    )


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


def run_recovery_cache_clear_bounded(
    package: str,
    *,
    root_info: android.RootInfo | None = None,
    deadline_s: float = RECOVERY_CACHE_CLEAR_DEADLINE_S,
    checker_pointer: Any | None = None,
) -> dict[str, object]:
    """Bounded recovery cache clear — NEVER hangs, ALWAYS advances to relaunch.

    Runs the single-package clear in a worker thread joined with a hard
    deadline.  On timeout/failure it records a result and returns so the
    recovery state machine can proceed to Reopening/Relaunching instead of
    freezing at Clearing Cache forever (probe p-2606bd7609).

    Safety: only ``package`` is touched.  On timeout we do not kill Termux,
    the checker loop, or other packages — the inner root shell is bounded by
    its own ``timeout_s`` and the orphaned worker thread is a daemon.
    """
    pkg = str(package or "").strip()
    started_at = time.time()
    if checker_pointer is not None:
        try:
            checker_pointer.begin_cache_clear(command_kind="su_find_delete")
        except Exception:  # noqa: BLE001
            pass

    def _finish(result: dict[str, Any], *, status: str, timed_out: bool,
                exit_code: int, error: str, kind: str) -> dict[str, object]:
        result = dict(result or {})
        result.update({
            "cache_clear_started_at": started_at,
            "cache_clear_finished_at": time.time(),
            "cache_clear_duration_ms": round((time.time() - started_at) * 1000.0, 1),
            "cache_clear_status": status,
            "cache_clear_timed_out": timed_out,
            "cache_clear_exit_code": exit_code,
            "cache_clear_error": error,
            "cache_clear_command_kind": kind,
        })
        if checker_pointer is not None:
            try:
                checker_pointer.record_cache_clear_result(
                    status=status,
                    exit_code=exit_code,
                    timed_out=timed_out,
                    error=error,
                    command_kind=kind,
                )
            except Exception:  # noqa: BLE001
                pass
        return result

    if not pkg:
        return _finish(
            {"success": False, "skipped": True, "skipped_reason": "invalid_package",
             "method": "recovery_single"},
            status="failed_continue_relaunch", timed_out=False,
            exit_code=1, error="invalid_package", kind="invalid",
        )

    holder: dict[str, Any] = {}

    def _work() -> None:
        try:
            # Inner root shell bounded a hair under the outer deadline.
            holder["result"] = android.clear_package_cache_recovery(
                pkg, root_info=root_info, timeout_s=max(5, int(deadline_s)),
            )
        except Exception as exc:  # noqa: BLE001
            holder["error"] = str(exc)[:200]

    worker = threading.Thread(
        target=_work, name=f"recovery-cache-clear-{pkg[:24]}", daemon=True
    )
    worker.start()
    worker.join(max(1.0, float(deadline_s) + 2.0))

    if worker.is_alive():
        # Hard timeout — advance to relaunch, leave the daemon to expire when
        # its bounded root shell is killed. Never block recovery.
        return _finish(
            {"success": False, "skipped": False, "method": "recovery_single",
             "error": "cache_clear_deadline_exceeded"},
            status="timeout_continue_relaunch", timed_out=True,
            exit_code=124, error="cache_clear_deadline_exceeded",
            kind="su_find_delete",
        )

    if "error" in holder:
        return _finish(
            {"success": False, "skipped": False, "method": "recovery_single",
             "error": holder["error"]},
            status="failed_continue_relaunch", timed_out=False,
            exit_code=1, error=holder["error"], kind="su_find_delete",
        )

    result = holder.get("result") or {}
    kind = str(result.get("command_kind") or "su_find_delete")
    if result.get("timed_out"):
        return _finish(result, status="timeout_continue_relaunch", timed_out=True,
                       exit_code=124, error=str(result.get("error") or "timed_out"),
                       kind=kind)
    if result.get("success"):
        return _finish(result, status="success", timed_out=False,
                       exit_code=0, error="", kind=kind)
    if result.get("skipped"):
        return _finish(
            result, status="skipped_continue_relaunch", timed_out=False,
            exit_code=0, error=str(result.get("skipped_reason") or ""), kind=kind,
        )
    return _finish(result, status="failed_continue_relaunch", timed_out=False,
                   exit_code=1, error=str(result.get("error") or "clear_failed"),
                   kind=kind)
