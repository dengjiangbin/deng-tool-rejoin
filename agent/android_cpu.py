"""Per-package and device CPU usage from ``/proc`` deltas.

Why this exists
───────────────
The status monitor used to show a single device-wide CPU number (parsed from
``top``), which on Android toybox is the **sum across all cores** (e.g. up to
800% on an 8-core phone).  That ``327%`` looked nonsensical, and the same value
was copied into every package row, so all packages showed identical CPU.

This module computes a true ``0-100%`` figure by sampling ``/proc/stat`` and
``/proc/<pid>/stat`` twice over a short interval:

* ``device_pct``   — busy share of total CPU capacity (0-100%).
* ``per_package_pct[pkg]`` — that package's share of total CPU capacity
  (Σ per-package ≈ device_pct, so the numbers add up).

Reading another UID's ``/proc/<pid>/stat`` needs root on modern Android; these
multi-instance setups already run rooted (same requirement as the RAM/PID
probes).  When unavailable, the affected value is simply omitted rather than
showing a wrong number.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Iterable


def cpu_core_count() -> int:
    try:
        return max(1, int(os.cpu_count() or 1))
    except Exception:  # noqa: BLE001
        return 1


def _read_proc_stat_total() -> tuple[int, int] | None:
    """Return ``(total_jiffies, idle_jiffies)`` summed across all cores."""
    try:
        first = Path("/proc/stat").read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None
    parts = first.split()
    if not parts or parts[0] != "cpu":
        return None
    nums = [int(x) for x in parts[1:] if x.lstrip("-").isdigit()]
    if len(nums) < 4:
        return None
    # fields: user nice system idle iowait irq softirq steal ...
    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
    total = sum(nums)
    if total <= 0:
        return None
    return total, idle


def _read_pid_cpu_jiffies(pid: str) -> int | None:
    """utime + stime (jiffies) for one pid, or ``None`` if unreadable."""
    try:
        data = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # comm (field 2) may contain spaces/parens — parse after the last ')'.
    rparen = data.rfind(")")
    if rparen < 0:
        return None
    rest = data[rparen + 1:].split()
    # rest[0] = state (field 3); utime = field 14 → rest[11]; stime = field 15 → rest[12]
    if len(rest) < 13:
        return None
    try:
        return int(rest[11]) + int(rest[12])
    except (TypeError, ValueError):
        return None


def _sum_pid_jiffies(pids: list[str]) -> int | None:
    total = 0
    ok = False
    for pid in pids:
        j = _read_pid_cpu_jiffies(pid)
        if j is not None:
            total += j
            ok = True
    return total if ok else None


def collect_cpu_usage(
    packages: Iterable[str],
    root_info: Any = None,
    *,
    sample_seconds: float = 0.5,
) -> dict[str, Any]:
    """Sample device + per-package CPU as a share of total capacity (0-100%)."""
    from .android import detect_root
    from .android_memory import get_package_pids

    info = root_info or detect_root()
    pkg_list = [str(p or "").strip() for p in packages if str(p or "").strip()]
    pkg_pids: dict[str, list[str]] = {}
    for pkg in pkg_list:
        try:
            pkg_pids[pkg] = get_package_pids(pkg, info)
        except Exception:  # noqa: BLE001
            pkg_pids[pkg] = []

    out: dict[str, Any] = {"device_pct": None, "per_package_pct": {}, "ncpu": cpu_core_count()}

    stat0 = _read_proc_stat_total()
    proc0 = {pkg: _sum_pid_jiffies(pids) for pkg, pids in pkg_pids.items()}

    time.sleep(max(0.05, float(sample_seconds)))

    stat1 = _read_proc_stat_total()
    if not (stat0 and stat1):
        return out
    dtotal = stat1[0] - stat0[0]
    didle = stat1[1] - stat0[1]
    if dtotal <= 0:
        return out

    out["device_pct"] = round(max(0.0, min(100.0, (dtotal - didle) * 100.0 / dtotal)), 1)

    for pkg, pids in pkg_pids.items():
        p0 = proc0.get(pkg)
        if p0 is None:
            continue
        p1 = _sum_pid_jiffies(pids)
        if p1 is None:
            continue
        dproc = max(0, p1 - p0)
        # Share of TOTAL capacity (all cores) → 0-100%, so Σ packages ≈ device.
        out["per_package_pct"][pkg] = round(min(100.0, dproc * 100.0 / dtotal), 1)

    return out
