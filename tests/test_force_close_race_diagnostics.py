"""Tests for force-close / crash detector race diagnostics."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.force_close_race import (  # noqa: E402
    ForceCloseRaceDetector,
    probe_force_close_race_snapshot,
    set_active_force_close_race_detector,
)
from agent.rjn_lifecycle_monitor import (  # noqa: E402
    PackageRjnState,
    RjnLifecycleMonitor,
    STATE_DEAD,
    STATE_ONLINE_CONFIRMED,
)

PKG_A = "com.roblox.client.a"
PKG_B = "com.roblox.client.b"


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class RaceRankingTests(unittest.TestCase):
    def test_winner_is_earliest_method(self) -> None:
        clock = FakeClock()
        monitor = MagicMock()
        monitor._lock = __import__("threading").RLock()
        monitor._states = {}
        monitor._uid_map = {}
        monitor._uid_to_package = {}
        monitor._pid_to_package = {}
        monitor._root_info = None
        monitor._process_check = MagicMock(return_value=(False, [], True))
        monitor.try_mark_force_close_dead = MagicMock(return_value=True)

        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / "race.jsonl"
            det = ForceCloseRaceDetector(
                [PKG_A],
                monitor=monitor,
                clock=clock.now,
                trace_path=trace,
            )
            det._session_active = True
            det._logcat_available = True
            row = det._packages[PKG_A]
            row.last_process_present_at = clock.now() - 2.0
            row.last_online_at = clock.now() - 10.0

            row.process_poll.first_at = clock.now()
            row.process_poll.latency_ms = 500.0
            clock.advance(0.3)
            row.logcat_crash.first_at = clock.now()
            clock.advance(0.5)
            row.current_detector.first_at = clock.now()
            row.current_detector.state = STATE_DEAD
            row.current_detector.reason = "process_missing"

            snap = det.probe_snapshot()
            winner = snap["packages"][PKG_A]["winner"]
            self.assertEqual(winner["method"], "process_poll")
            self.assertIsNotNone(winner["at"])
            self.assertLess(winner["delta_ms_vs_current_detector"], 0)


class StaleHeartbeatTests(unittest.TestCase):
    def test_stale_hb_cannot_confirm_online_when_process_missing(self) -> None:
        mon = RjnLifecycleMonitor([PKG_A])
        mon.start_session()
        row = mon._states[PKG_A]
        row.internal_state = STATE_ONLINE_CONFIRMED
        row.ingame_hb_ever = True
        row.online_since = time.time() - 60

        with patch.object(mon, "_process_check", return_value=(False, [], True)):
            mon._confirm_online_evidence(PKG_A, time.time(), source="push_heartbeat")

        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED)
        self.assertIn("process_missing_blocks_online_confirm", row.last_rejected_signal_reason)

    def test_process_poll_marks_dead_when_online(self) -> None:
        clock = FakeClock()
        monitor = RjnLifecycleMonitor([PKG_A])
        monitor._states[PKG_A] = PackageRjnState(package=PKG_A)
        monitor._states[PKG_A].internal_state = STATE_ONLINE_CONFIRMED
        monitor._states[PKG_A].ingame_hb_ever = True
        monitor._states[PKG_A].online_since = clock.now() - 30

        with patch.object(monitor, "_process_check", return_value=(False, [], True)), \
             patch.object(monitor, "try_mark_force_close_dead", return_value=True) as mark:
            det = ForceCloseRaceDetector([PKG_A], monitor=monitor, clock=clock.now)
            det._packages[PKG_A].last_process_present_at = clock.now() - 1.0
            det._process_poll_once(PKG_A, clock.now())

        mark.assert_called_once()


class LogcatMultilineFatalTests(unittest.TestCase):
    def test_fatal_block_maps_only_to_matching_package(self) -> None:
        monitor = MagicMock()
        monitor._lock = __import__("threading").RLock()
        monitor._uid_to_package = {}
        monitor._pid_to_package = {"5555": PKG_A}
        monitor._states = {}
        monitor._uid_map = {}

        det = ForceCloseRaceDetector([PKG_A, PKG_B], monitor=monitor)
        det._packages[PKG_A].last_process_present_at = time.time() - 1.0
        det._packages[PKG_B].last_process_present_at = time.time() - 1.0

        det._handle_logcat_line(
            "1000.0 07-01 12:00:00.000 10001 5555 5555 E AndroidRuntime: FATAL EXCEPTION: main"
        )
        det._handle_logcat_line(
            f"1000.1 07-01 12:00:00.100 10001 5555 5555 E AndroidRuntime: Process: {PKG_A}"
        )
        det._handle_logcat_line(
            "1000.2 07-01 12:00:00.200 10001 5555 5555 I ActivityManager: am_crash"
        )

        self.assertGreater(det._packages[PKG_A].logcat_crash.first_at, 0)
        self.assertEqual(det._packages[PKG_B].logcat_crash.first_at, 0.0)
        self.assertIn("fatal", det._packages[PKG_A].logcat_crash.evidence)


class AdbUnavailableProbeTests(unittest.TestCase):
    def test_adb_unavailable_in_probe_without_crash(self) -> None:
        set_active_force_close_race_detector(None)
        with patch("agent.force_close_race.shutil.which", return_value=None):
            snap = probe_force_close_race_snapshot()
        self.assertIn("adapters", snap)
        self.assertFalse(snap["adapters"]["adb_shell"]["available"])

    def test_live_detector_adb_unavailable(self) -> None:
        monitor = MagicMock()
        monitor._lock = __import__("threading").RLock()
        monitor._states = {}
        monitor._uid_map = {}
        monitor._uid_to_package = {}
        monitor._pid_to_package = {}
        monitor._root_info = None
        monitor._process_check = MagicMock(return_value=(True, ["123"], False))
        monitor.try_mark_force_close_dead = MagicMock(return_value=False)

        det = ForceCloseRaceDetector([PKG_A], monitor=monitor)
        det._session_active = True
        det._adb_available = False
        det._adb_error = "adb_not_in_path"
        for row in det._packages.values():
            row.adb_shell.available = False
            row.adb_shell.error = "adb_not_in_path"
        set_active_force_close_race_detector(det)
        try:
            snap = probe_force_close_race_snapshot()
            adb = snap["packages"][PKG_A]["methods"]["adb_shell"]
            self.assertFalse(adb["available"])
            self.assertEqual(adb["error"], "adb_not_in_path")
        finally:
            set_active_force_close_race_detector(None)


class ForceCloseRaceSchemaTests(unittest.TestCase):
    REQUIRED_TOP = {
        "enabled",
        "sample_window_seconds",
        "packages",
        "recent_events",
    }
    REQUIRED_PKG_METHODS = {"adb_shell", "logcat_crash", "process_poll", "current_detector"}
    REQUIRED_WINNER = {"method", "at", "delta_ms_vs_current_detector"}

    def test_probe_schema_shape(self) -> None:
        from agent import probe as P

        probe = P.collect_probe()
        self.assertIn("force_close_race", probe)
        fcr = probe["force_close_race"]
        self.assertTrue(isinstance(fcr, dict))
        for key in self.REQUIRED_TOP:
            self.assertIn(key, fcr)
        self.assertTrue(isinstance(fcr.get("recent_events"), list))

    def test_live_snapshot_package_schema(self) -> None:
        monitor = MagicMock()
        monitor._lock = __import__("threading").RLock()
        monitor._states = {}
        monitor._uid_map = {}
        monitor._uid_to_package = {}
        monitor._pid_to_package = {}
        monitor._root_info = None
        monitor._process_check = MagicMock(return_value=(True, ["1"], False))
        monitor.try_mark_force_close_dead = MagicMock(return_value=False)

        det = ForceCloseRaceDetector([PKG_A], monitor=monitor)
        det._session_active = True
        det._packages[PKG_A].last_process_present_at = time.time()
        snap = det.probe_snapshot()
        self.assertIn("adapters", snap)
        pkg = snap["packages"][PKG_A]
        self.assertIn("methods", pkg)
        self.assertEqual(set(pkg["methods"].keys()), self.REQUIRED_PKG_METHODS)
        self.assertEqual(set(pkg["winner"].keys()), self.REQUIRED_WINNER)


if __name__ == "__main__":
    unittest.main()
