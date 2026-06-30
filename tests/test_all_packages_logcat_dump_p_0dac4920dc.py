"""Regression: all-packages logcat dump instead of round-robin gating (p-0dac4920dc).

Probe showed clone #6 waiting ~76s for its logcat dump turn while clone #1
consumed the full watchdog round.  Every package must get a PID-scoped dump on
the same cadence, independent of round-robin order.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.rjn_lifecycle_monitor import (
    ALL_PACKAGES_DUMP_INTERVAL_SECONDS,
    RjnLifecycleMonitor,
    STATE_ONLINE_CONFIRMED,
)

PKG_A = "com.moons.litesc"
PKG_B = "com.moons.litesd"
PKG_C = "com.moons.litese"


class AllPackagesLogcatDumpTests(unittest.TestCase):
    def test_scan_all_dumps_every_package_same_cycle(self) -> None:
        pkgs = [PKG_A, PKG_B, PKG_C]
        m = RjnLifecycleMonitor(pkgs)
        m.start_session()
        m._ensure_logcat_stream = lambda: None  # type: ignore[assignment]
        dumped: list[str] = []

        def _fake_dump(pkg: str, now: float, *, force: bool = False) -> None:
            dumped.append(pkg)

        pids = {PKG_A: ["111"], PKG_B: ["222"], PKG_C: ["333"]}
        m._process_check = lambda pkg: (True, pids.get(pkg, []), False)  # type: ignore[assignment]

        with patch.object(m, "_scan_logcat_dump", side_effect=_fake_dump):
            m.scan_all_packages_logcat_dump(force=True)

        self.assertEqual(sorted(dumped), sorted(pkgs))

    def test_scan_all_respects_interval_throttle(self) -> None:
        m = RjnLifecycleMonitor([PKG_A])
        m.start_session()
        m._process_check = lambda pkg: (True, ["111"], False)  # type: ignore[assignment]
        with patch.object(m, "_scan_logcat_dump") as dump:
            m.scan_all_packages_logcat_dump(force=True)
            m.scan_all_packages_logcat_dump(force=False)
        dump.assert_called_once()

    def test_bulk_scan_skips_per_package_dump_in_evaluate(self) -> None:
        m = RjnLifecycleMonitor([PKG_A])
        m.start_session()
        m._process_check = lambda pkg: (True, ["111"], False)  # type: ignore[assignment]
        m._ensure_logcat_stream = lambda: None  # type: ignore[assignment]
        m._poll_recent_logcat = lambda: None  # type: ignore[assignment]
        m._states[PKG_A].internal_state = STATE_ONLINE_CONFIRMED
        m._states[PKG_A].ingame_hb_ever = True
        m._states[PKG_A].last_ingame_hb_at = time.time()
        m._last_all_packages_dump_at = time.time()

        with patch.object(m, "_scan_logcat_dump") as dump:
            m.evaluate_package(PKG_A, fast_push=False)
        dump.assert_not_called()

    def test_force_close_checked_for_all_packages_in_bulk_scan(self) -> None:
        m = RjnLifecycleMonitor([PKG_A, PKG_B])
        m.start_session()
        m._process_check = lambda pkg: (False, [], True)  # type: ignore[assignment]
        marked: list[str] = []
        m.try_mark_force_close_dead = lambda pkg, **kw: marked.append(pkg) or True  # type: ignore[method-assign]

        m.scan_all_packages_logcat_dump(force=True)
        self.assertEqual(sorted(marked), sorted([PKG_A, PKG_B]))

    def test_interval_constant_targets_sub_15s(self) -> None:
        self.assertLessEqual(ALL_PACKAGES_DUMP_INTERVAL_SECONDS, 5.0)


if __name__ == "__main__":
    unittest.main()
