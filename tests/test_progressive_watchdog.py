"""Progressive watchdog: monitor opened packages while stagger continues."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.supervisor import STATUS_LAUNCHING, STATUS_READY, WatchdogSupervisor


class ProgressiveWatchdogTests(unittest.TestCase):
    def test_opened_packages_skips_ready_clones(self) -> None:
        sup = WatchdogSupervisor(
            [{"package": "com.pkg.a"}, {"package": "com.pkg.b"}],
            {"supervisor": {"detection_worker_count": 2}},
            initial_status={"com.pkg.a": STATUS_LAUNCHING, "com.pkg.b": STATUS_READY},
        )
        sup.mark_package_launched("com.pkg.a")
        self.assertEqual(sup._opened_packages(), ["com.pkg.a"])
        self.assertTrue(sup._watchdog_monitoring_active())
        self.assertTrue(sup._package_awaiting_first_open("com.pkg.b"))

    def test_unopened_clones_stay_awaiting_until_batch_latch_released(self) -> None:
        sup = WatchdogSupervisor(
            [{"package": "com.pkg.a"}, {"package": "com.pkg.b"}],
            {},
            initial_status={"com.pkg.a": STATUS_LAUNCHING, "com.pkg.b": STATUS_READY},
        )
        sup.mark_package_launched("com.pkg.a")
        sup._last_launched_at["com.pkg.b"] = 999.0
        self.assertTrue(sup._package_awaiting_first_open("com.pkg.b"))
        sup.mark_all_launches_completed()
        self.assertFalse(sup._package_awaiting_first_open("com.pkg.b"))

    def test_prefetch_uses_parallel_workers_for_multiple_opened(self) -> None:
        sup = WatchdogSupervisor(
            [{"package": "com.pkg.a"}, {"package": "com.pkg.b"}],
            {"supervisor": {"detection_worker_count": 2}},
            initial_status={"com.pkg.a": STATUS_LAUNCHING, "com.pkg.b": STATUS_LAUNCHING},
        )
        sup.mark_package_launched("com.pkg.a")
        sup.mark_package_launched("com.pkg.b")
        with patch.object(sup, "_needs_launching_evaluation", return_value=False), \
             patch.object(sup, "_detect_package_state", side_effect=[
                 ("Online", {"reason": "a"}),
                 ("Online", {"reason": "b"}),
             ]) as detect:
            prefetched = sup._prefetch_package_detection(["com.pkg.a", "com.pkg.b"])
        self.assertEqual(set(prefetched), {"com.pkg.a", "com.pkg.b"})
        self.assertEqual(detect.call_count, 2)


if __name__ == "__main__":
    unittest.main()
