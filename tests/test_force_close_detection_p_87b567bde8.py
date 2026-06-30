"""Regression tests for probe p-87b567bde8.

User feedback:
1. Changing game after 10h was detected but with the lobby reason; the lobby
   (285) reason should read "Account stays too long in the lobby/wrong server"
   (covered in test_disconnect_codes_and_deeplink_resolve_2026_06_28).
2. CRITICAL: a manually force-closed package was NOT detected as dead for ~1 hour.

Root causes of #2:
  * The logcat live-stream reader decoded with STRICT utf-8, so a single non-utf8
    byte (0xc0, common in Roblox player-name logs) raised UnicodeDecodeError and
    killed the whole reader thread, degrading detection to slow fallbacks.
  * The process-missing kill path gated on ``_was_ever_online_confirmed`` (which
    only checked the slow-scrape ``last_positive_online_evidence_at``) and
    otherwise required the state to be EXACTLY ONLINE_CONFIRMED. A clone proven
    online purely by the in-game logcat heartbeat (the normal cloud-phone case)
    left that timestamp unset, so after a force-close it fell through every
    branch and was never recovered.

These tests verify the force-close kill path now fires within
PROCESS_MISSING_CONFIRM checks for a heartbeat-online clone, is suppressed during
the tool's own relaunch, and that the stream reader survives a malformed line.
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

from agent.constants import DATA_DIR
from agent import rjn_lifecycle_monitor as rlm
from agent.rjn_lifecycle_monitor import (
    RjnLifecycleMonitor,
    STATE_DEAD,
    STATE_ONLINE_CONFIRMED,
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


def _online_via_heartbeat(mon: RjnLifecycleMonitor, pkg: str) -> None:
    # Mirror the cloud-phone reality: online proven ONLY by the in-game logcat
    # heartbeat (no slow-scrape positive evidence timestamp).
    mon.ingest_push_heartbeat(
        pkg, alive=True, place_id=121864768012064, universe_id=6701277882, at=time.time()
    )
    assert mon._states[pkg].internal_state == STATE_ONLINE_CONFIRMED


class ForceCloseDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def tearDown(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def test_heartbeat_online_counts_as_ever_online(self) -> None:
        pkg = "com.pkg.fc1"
        mon = _monitor(pkg)
        _online_via_heartbeat(mon, pkg)
        row = mon._states[pkg]
        # The slow-scrape timestamp is intentionally unset for a heartbeat clone.
        self.assertTrue(mon._was_ever_online_confirmed(row))

    def test_force_close_detected_within_confirm_checks(self) -> None:
        pkg = "com.pkg.fc2"
        mon = _monitor(pkg)
        _online_via_heartbeat(mon, pkg)
        # User force-closes the clone: pidof now returns nothing. Not launching.
        with patch.object(mon, "_process_check", return_value=(False, [])), \
             patch.object(mon, "_dump_pkg_logcat", return_value=[]):
            ev = None
            for _ in range(PROCESS_MISSING_CONFIRM):
                ev = mon.evaluate_package(pkg)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.internal_state, STATE_DEAD)
        self.assertFalse(ev.is_online_confirmed)
        self.assertEqual(mon._states[pkg].last_transition_reason, "process_missing")

    def test_force_close_after_disconnect_state_still_detected(self) -> None:
        # The exact dead-end that hid the bug: state is DISCONNECTED (not the
        # required ONLINE_CONFIRMED) and the slow-scrape timestamp is unset.
        pkg = "com.pkg.fc3"
        mon = _monitor(pkg)
        _online_via_heartbeat(mon, pkg)
        row = mon._states[pkg]
        row.internal_state = rlm.STATE_DISCONNECTED
        row.last_positive_online_evidence_at = 0.0  # only heartbeat proved online
        with patch.object(mon, "_process_check", return_value=(False, [])), \
             patch.object(mon, "_dump_pkg_logcat", return_value=[]):
            ev = None
            for _ in range(PROCESS_MISSING_CONFIRM):
                ev = mon.evaluate_package(pkg)
        self.assertEqual(ev.internal_state, STATE_DEAD)

    def test_first_launch_process_gap_not_marked_dead(self) -> None:
        # A package still on its FIRST launch (never reached a live server) must
        # tolerate a transient process gap and not be force-killed prematurely.
        pkg = "com.pkg.fc4"
        mon = _monitor(pkg)
        mon.note_launch_watchdog(pkg, relaunch=False)  # watchdog_active, never online
        with patch.object(mon, "_process_check", return_value=(False, [])), \
             patch.object(mon, "_dump_pkg_logcat", return_value=[]):
            ev = None
            for _ in range(PROCESS_MISSING_CONFIRM + 2):
                ev = mon.evaluate_package(pkg)
        self.assertNotEqual(ev.internal_state, STATE_DEAD)

    def test_never_online_idle_process_not_marked_dead(self) -> None:
        # A configured package that never reached a live server and is not being
        # launched should not be force-recovered out of nowhere.
        pkg = "com.pkg.fc5"
        mon = _monitor(pkg)
        row = mon._states[pkg]
        row.watchdog_active = False
        row.launch_started_at = 0.0
        with patch.object(mon, "_process_check", return_value=(False, [])), \
             patch.object(mon, "_dump_pkg_logcat", return_value=[]):
            ev = None
            for _ in range(PROCESS_MISSING_CONFIRM + 2):
                ev = mon.evaluate_package(pkg)
        self.assertNotEqual(ev.internal_state, STATE_DEAD)


class _FakeStdout:
    def __init__(self, items: list) -> None:
        self._items = items
        self._i = 0

    def readline(self):
        if self._i >= len(self._items):
            return ""
        item = self._items[self._i]
        self._i += 1
        if isinstance(item, Exception):
            raise item
        return item

    def exhausted(self) -> bool:
        return self._i >= len(self._items)


class _FakeProc:
    def __init__(self, stdout: _FakeStdout) -> None:
        self.stdout = stdout
        self.pid = 4242

    def poll(self):
        return 0 if self.stdout.exhausted() else None

    def kill(self) -> None:
        pass


class LogcatStreamResilienceTests(unittest.TestCase):
    def test_reader_survives_malformed_line(self) -> None:
        pkg = "com.pkg.stream"
        mon = _monitor(pkg)
        handled: list[str] = []
        good = (
            "06-30 10:00:00.000 10104 1 1 I Roblox  : [FLog::Output] "
            "DENGRJN_HB|1|1|2|job|1"
        )
        # A read that raises (the old strict-utf8 failure) followed by a good line:
        # the loop must NOT die on the bad read and must still process the good one.
        proc = _FakeProc(_FakeStdout([UnicodeDecodeError("utf-8", b"\xc0", 0, 1, "bad"), good]))
        with patch.object(rlm.subprocess, "Popen", return_value=proc), \
             patch.object(mon, "_handle_logcat_line", side_effect=lambda ln: handled.append(ln)):
            mon._logcat_reader_loop()
        self.assertIn(good, handled)
        self.assertTrue(any("logcat_line" in e for e in mon._detector_errors))


if __name__ == "__main__":
    unittest.main()
