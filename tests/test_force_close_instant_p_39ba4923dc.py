"""Regression tests for probe p-39ba4923dc.

User reports:
1. Force-close not instant (should match the ~1s online-detection speed).
2. Last package stuck at "Launching" too long (dump-scan round explosion).
3. 2nd+ force-close not detected after first relaunch cycle.

Root causes:
  * _process_check used `pidof` (subprocess, 100-500ms) for every check.
    When all PIDs are known, we can use /proc/<pid> existence (microseconds).
    This makes force-close detection instant regardless of round size.
  * logcat -d timeout was 8s; with 6 packages each potentially dumping, a
    full round took up to 48s. The last package only got its status updated
    once per ~48s round. Reduced to 3s.
  * After first dead→relaunch, note_launch_watchdog resets ingame_hb_ever,
    online_since, and last_positive_online_evidence_at (all cleared by the
    dead transition). A user force-close AFTER the relaunched process appeared
    but BEFORE gamejoinloadtime fired left ever_in_game=False and
    watchdog_active=True → streak reset → never detected. Fixed by tracking
    process_seen_since_launch: if the process appeared post-launch and then
    disappears, it's a user force-close.
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.constants import DATA_DIR
from agent import rjn_lifecycle_monitor as rlm
from agent.rjn_lifecycle_monitor import (
    RjnLifecycleMonitor,
    STATE_DEAD,
    STATE_ONLINE_CONFIRMED,
    STATE_RELAUNCHING,
    PROCESS_MISSING_CONFIRM,
)


def _monitor(pkg: str, uid: str = "10104") -> RjnLifecycleMonitor:
    mon = RjnLifecycleMonitor([pkg])
    mon._uid_map = {pkg: uid}
    mon._monitor_started_at = time.time() - 600
    row = mon._states[pkg]
    row.uid = uid
    row.launch_started_at = time.time() - 540
    return mon


def _go_online(mon: RjnLifecycleMonitor, pkg: str, pid: str = "9999") -> None:
    """Simulate package going online via in-game heartbeat (primary hot lane)."""
    row = mon._states[pkg]
    row.pids = [pid]
    row.process_exists = True
    mon.ingest_push_heartbeat(
        pkg, alive=True, place_id=121864768012064, universe_id=6701277882, at=time.time()
    )
    assert row.internal_state == STATE_ONLINE_CONFIRMED


class ProcFastPathTests(unittest.TestCase):
    """_process_check uses package-scoped cmdline/pidof (not stale /proc cache)."""

    def setUp(self):
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def tearDown(self):
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def test_proc_check_returns_false_when_pid_gone(self) -> None:
        pkg = "com.pkg.proc1"
        mon = _monitor(pkg)
        row = mon._states[pkg]
        row.pids = ["999999999"]
        row.online_since = time.time()
        with patch.object(mon, "_authoritative_package_pids", return_value=[]):
            exists, pids, definitive = mon._process_check(pkg)
        self.assertFalse(exists)
        self.assertEqual(pids, [])
        self.assertTrue(definitive)

    def test_proc_check_rediscovers_after_stale_pid_cache(self) -> None:
        """Roblox PID rotation must not leave a live clone stuck process_missing."""
        pkg = "com.pkg.proc1b"
        mon = _monitor(pkg)
        row = mon._states[pkg]
        row.pids = ["999999999"]  # stale
        with patch.object(mon, "_authoritative_package_pids", return_value=["54321"]):
            exists, pids, definitive = mon._process_check(pkg)
        self.assertTrue(exists)
        self.assertIn("54321", pids)
        self.assertFalse(definitive)

    def test_proc_check_returns_true_when_authoritative_finds_pid(self) -> None:
        pkg = "com.pkg.proc2"
        mon = _monitor(pkg)
        row = mon._states[pkg]
        row.pids = ["12345"]
        with patch.object(mon, "_authoritative_package_pids", return_value=["12345"]):
            exists, pids, _definitive = mon._process_check(pkg)
        self.assertTrue(exists)
        self.assertIn("12345", pids)

    def test_proc_check_falls_back_to_pidof_when_no_known_pids(self) -> None:
        pkg = "com.pkg.proc3"
        mon = _monitor(pkg)
        row = mon._states[pkg]
        row.pids = []  # no known PIDs
        with patch.object(mon, "_authoritative_package_pids", return_value=["12345"]):
            exists, pids, _definitive = mon._process_check(pkg)
        self.assertTrue(exists)
        self.assertIn("12345", pids)


class ForceCloseDuringRelaunchTests(unittest.TestCase):
    """process_seen_since_launch detects user force-close inside the relaunch window."""

    def setUp(self):
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def tearDown(self):
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def test_process_seen_since_launch_set_on_discovery(self) -> None:
        pkg = "com.pkg.rll1"
        mon = _monitor(pkg)
        mon.note_launch_watchdog(pkg, relaunch=True)
        row = mon._states[pkg]
        self.assertFalse(row.process_seen_since_launch)
        # Process now appears (Roblox started loading) — simulate via evaluate
        with patch.object(mon, "_process_check", return_value=(True, ["8888"])), \
             patch.object(mon, "_dump_pkg_logcat", return_value=[]):
            mon.evaluate_package(pkg)
        self.assertTrue(row.process_seen_since_launch)

    def _full_relaunch_setup(self, pkg: str, mon: RjnLifecycleMonitor, pid: str = "8001") -> None:
        """Simulate the complete dead→relaunch cycle: go online, then dead transition,
        then note_launch_watchdog. This is the real tool flow that clears online_since."""
        _go_online(mon, pkg, pid)
        row = mon._states[pkg]
        # Simulate what _transition(STATE_DEAD, offline=True) does:
        row.online_since = 0.0
        row.last_positive_online_evidence_at = 0.0
        row.last_gamejoinloadtime_at = 0.0
        row.internal_state = rlm.STATE_DEAD
        # note_launch_watchdog (resets heartbeat tracking + sets watchdog)
        mon.note_launch_watchdog(pkg, relaunch=True)
        row.launch_started_at = time.time() - 5  # fresh launch, not timed out

    def test_force_close_after_process_seen_detected_as_dead(self) -> None:
        """Force-close after process appeared (loading screen) must be caught."""
        pkg = "com.pkg.rll2"
        mon = _monitor(pkg)
        self._full_relaunch_setup(pkg, mon)
        row = mon._states[pkg]
        # Simulate that the process was seen briefly since launch (Roblox started loading)
        row.process_seen_since_launch = True
        # Now user force-closes — process disappears
        with patch.object(mon, "_process_check", return_value=(False, [])), \
             patch.object(mon, "_dump_pkg_logcat", return_value=[]):
            ev = None
            for _ in range(PROCESS_MISSING_CONFIRM):
                ev = mon.evaluate_package(pkg)
        self.assertEqual(ev.internal_state, STATE_DEAD)

    def test_tool_am_force_stop_gap_not_marked_dead(self) -> None:
        """Tool's own am-force-stop gap (process never seen since launch) must NOT trigger dead."""
        pkg = "com.pkg.rll3"
        mon = _monitor(pkg)
        self._full_relaunch_setup(pkg, mon)
        row = mon._states[pkg]
        # Process not yet seen since launch (am-force-stop already ran, am-start pending)
        self.assertFalse(row.process_seen_since_launch)
        # Process is gone (am-force-stop already ran, Roblox not yet opened)
        with patch.object(mon, "_process_check", return_value=(False, [])), \
             patch.object(mon, "_dump_pkg_logcat", return_value=[]):
            ev = None
            for _ in range(PROCESS_MISSING_CONFIRM + 2):
                ev = mon.evaluate_package(pkg)
        self.assertNotEqual(ev.internal_state, STATE_DEAD)

    def test_second_force_close_after_full_relaunch_cycle_detected(self) -> None:
        """2nd force-close (after full relaunch + online) must be detected."""
        pkg = "com.pkg.rll4"
        mon = _monitor(pkg)
        _go_online(mon, pkg, "5001")
        # Simulate 1st force-close + relaunch + 2nd online session
        mon._states[pkg].online_since = 0.0
        mon._states[pkg].last_positive_online_evidence_at = 0.0
        mon._states[pkg].ingame_hb_ever = False
        mon._states[pkg].watchdog_active = False
        mon._states[pkg].process_seen_since_launch = False
        # 2nd session: package comes back online
        _go_online(mon, pkg, "5002")
        # 2nd force-close: process disappears
        with patch.object(mon, "_process_check", return_value=(False, [])), \
             patch.object(mon, "_dump_pkg_logcat", return_value=[]):
            ev = None
            for _ in range(PROCESS_MISSING_CONFIRM):
                ev = mon.evaluate_package(pkg)
        self.assertEqual(ev.internal_state, STATE_DEAD)


class DumpTimeoutTests(unittest.TestCase):
    """logcat -d timeout is 3s (was 8s) to prevent round-time explosion."""

    def test_dump_timeout_is_three_seconds(self) -> None:
        """Verify the dump uses a 3s timeout so 6-package rounds take ≤18s max."""
        pkg = "com.pkg.timeout1"
        mon = _monitor(pkg)
        row = mon._states[pkg]
        row.pids = ["12345"]
        row.last_dump_scan_at = 0.0

        captured_timeout: list[int] = []

        def fake_run_command(args, *, timeout=8):
            captured_timeout.append(timeout)
            return type("R", (), {"ok": True, "stdout": ""})()

        with patch.object(rlm.android, "run_command", side_effect=fake_run_command):
            mon._dump_pkg_logcat(["12345"])

        self.assertTrue(captured_timeout, "run_command should have been called")
        self.assertEqual(captured_timeout[0], 3, f"Expected 3s timeout, got {captured_timeout[0]}s")


if __name__ == "__main__":
    unittest.main()
