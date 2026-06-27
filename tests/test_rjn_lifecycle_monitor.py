"""Unit tests for rjn.txt-style UID logcat lifecycle detection."""

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
from agent.rjn_lifecycle_monitor import (
    RjnLifecycleMonitor,
    STATE_DISCONNECTED,
    STATE_DEAD,
    STATE_FAILED,
    STATE_LAUNCHING,
    STATE_ONLINE_CONFIRMED,
    STATE_RELAUNCHING,
    STATE_TELEPORTING,
    resolve_package_uid,
)
from agent.status_monitor_runtime import load_online_since


SAMPLE_DUMPSYS = (
    "Package [com.roblox.client] (com.roblox.client):\n"
    "  userId=12345\n"
)


class RjnUidMapTests(unittest.TestCase):
    def setUp(self) -> None:
        path = DATA_DIR / "status-monitor-runtime-state.json"
        path.unlink(missing_ok=True)

    def tearDown(self) -> None:
        path = DATA_DIR / "status-monitor-runtime-state.json"
        path.unlink(missing_ok=True)

    def test_uid_map_parsing(self) -> None:
        with patch(
            "agent.android.run_android_command",
            return_value=type("R", (), {"ok": True, "stdout": SAMPLE_DUMPSYS, "stderr": ""})(),
        ):
            res = resolve_package_uid("com.roblox.client")
        self.assertEqual(res.uid, "12345")
        self.assertIsNone(res.error)


class RjnLogcatFilterTests(unittest.TestCase):
    def test_ignores_unrelated_uid(self) -> None:
        mon = RjnLifecycleMonitor(["com.roblox.client"])
        mon._uid_map = {"com.roblox.client": "12345"}
        mon._uid_to_package = {"12345": "com.roblox.client"}
        mon._monitor_started_at = time.time() - 10
        mon._handle_logcat_line("uid=99999 gamejoinloadtime foo")
        row = mon._states["com.roblox.client"]
        self.assertNotEqual(row.internal_state, STATE_ONLINE_CONFIRMED)

    def test_accepts_target_uid_gamejoin(self) -> None:
        mon = RjnLifecycleMonitor(["com.roblox.client"])
        mon._uid_map = {"com.roblox.client": "12345"}
        mon._uid_to_package = {"12345": "com.roblox.client"}
        mon._monitor_started_at = time.time() - 10
        t0 = time.time()
        with patch.object(mon, "_process_check", return_value=(True, ["999"])):
            mon._handle_logcat_line("uid=12345 gamejoinloadtime=500")
            ev = mon.evaluate_package("com.roblox.client")
        self.assertTrue(ev.is_online_confirmed)
        self.assertEqual(mon._states["com.roblox.client"].internal_state, STATE_ONLINE_CONFIRMED)
        online_since, row = load_online_since("com.roblox.client")
        self.assertIsNotNone(online_since)
        self.assertEqual(row.get("runtime_source"), "gamejoinloadtime")
        self.assertGreaterEqual(online_since or 0, t0 - 1)


class RjnRuntimeTests(unittest.TestCase):
    def test_first_launch_runtime_starts_at_gamejoin_not_launch(self) -> None:
        t0 = 1_000_000.0
        mon = RjnLifecycleMonitor(["com.pkg.a"])
        mon._uid_map = {"com.pkg.a": "100"}
        mon._uid_to_package = {"100": "com.pkg.a"}
        mon._states["com.pkg.a"].uid = "100"
        mon._monitor_started_at = t0
        mon.begin_launch_watchdog("com.pkg.a", relaunch=False)
        with patch.object(mon, "_process_check", return_value=(True, ["1"])):
            ev_early = mon.evaluate_package("com.pkg.a")
        self.assertFalse(ev_early.is_online_confirmed)
        self.assertEqual(ev_early.public_status, "Launching")
        join_at = t0 + 30.0
        with patch("agent.rjn_lifecycle_monitor.time.time", return_value=join_at):
            mon._apply_phrase(
                "com.pkg.a",
                "gamejoinloadtime",
                join_at,
                type("E", (), {"action_taken": ""})(),
            )
        with patch.object(mon, "_process_check", return_value=(True, ["1"])):
            ev_late = mon.evaluate_package("com.pkg.a")
        self.assertTrue(ev_late.is_online_confirmed)
        online_since, _ = load_online_since("com.pkg.a")
        self.assertEqual(online_since, join_at)

    def test_relaunch_runtime_starts_at_gamejoin(self) -> None:
        t0 = 2_000_000.0
        mon = RjnLifecycleMonitor(["com.pkg.b"])
        mon._uid_map = {"com.pkg.b": "200"}
        mon._uid_to_package = {"200": "com.pkg.b"}
        mon.begin_launch_watchdog("com.pkg.b", relaunch=True)
        join_at = t0 + 20.0
        with patch("agent.rjn_lifecycle_monitor.time.time", return_value=join_at):
            mon._apply_phrase(
                "com.pkg.b",
                "gamejoinloadtime",
                join_at,
                type("E", (), {"action_taken": ""})(),
            )
        with patch.object(mon, "_process_check", return_value=(True, ["2"])):
            ev = mon.evaluate_package("com.pkg.b")
        self.assertTrue(ev.is_online_confirmed)
        online_since, _ = load_online_since("com.pkg.b")
        self.assertEqual(online_since, join_at)


class RjnDisconnectTests(unittest.TestCase):
    def test_with_reason_marks_disconnected(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.c"])
        mon._uid_map = {"com.pkg.c": "300"}
        mon._uid_to_package = {"300": "com.pkg.c"}
        mon._states["com.pkg.c"].internal_state = STATE_ONLINE_CONFIRMED
        mon._states["com.pkg.c"].last_gamejoinloadtime_at = time.time() - 60
        mon._states["com.pkg.c"].online_since = time.time() - 60
        t1 = time.time()
        mon._apply_phrase(
            "com.pkg.c",
            "with reason",
            t1,
            type("E", (), {"action_taken": ""})(),
        )
        with patch.object(mon, "_process_check", return_value=(True, ["3"])):
            ev = mon.evaluate_package("com.pkg.c")
        self.assertFalse(ev.is_online_confirmed)
        self.assertEqual(ev.internal_state, STATE_DISCONNECTED)

    def test_idle_disconnect_ui_while_online_marks_disconnected(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.idle"])
        mon._uid_map = {"com.pkg.idle": "301"}
        row = mon._states["com.pkg.idle"]
        row.internal_state = STATE_ONLINE_CONFIRMED
        row.last_positive_online_evidence_at = time.time() - 120
        row.online_since = time.time() - 120
        row.online_evidence_source = "activity_in_game"
        row.last_disconnect_scan_at = 0.0
        with patch.object(mon, "_process_check", return_value=(True, ["4"])), \
             patch.object(mon, "_detect_live_disconnect", return_value=("idle_disconnect_278", "Error Code: 278 idle")):
            ev = mon.evaluate_package("com.pkg.idle")
        self.assertFalse(ev.is_online_confirmed)
        self.assertEqual(ev.internal_state, STATE_DISCONNECTED)
        self.assertEqual(row.last_transition_reason, "idle_disconnect_278")
        self.assertIn(
            "idle",
            ev.detail.get("reason_user_friendly", "").lower(),
        )

    def test_doteleport_then_gamejoin(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.d"])
        mon._uid_map = {"com.pkg.d": "400"}
        mon._uid_to_package = {"400": "com.pkg.d"}
        mon._states["com.pkg.d"].internal_state = STATE_ONLINE_CONFIRMED
        mon._states["com.pkg.d"].last_gamejoinloadtime_at = 100.0
        mon._states["com.pkg.d"].online_since = 100.0
        mon._apply_phrase(
            "com.pkg.d",
            "doTeleport",
            200.0,
            type("E", (), {"action_taken": ""})(),
        )
        self.assertEqual(mon._states["com.pkg.d"].internal_state, STATE_TELEPORTING)
        mon._apply_phrase(
            "com.pkg.d",
            "gamejoinloadtime",
            220.0,
            type("E", (), {"action_taken": ""})(),
        )
        self.assertEqual(mon._states["com.pkg.d"].internal_state, STATE_ONLINE_CONFIRMED)
        online_since, _ = load_online_since("com.pkg.d")
        self.assertEqual(online_since, 220.0)


class RjnForceCloseTests(unittest.TestCase):
    def test_process_missing_marks_dead(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.e"])
        mon._uid_map = {"com.pkg.e": "500"}
        mon._uid_to_package = {"500": "com.pkg.e"}
        row = mon._states["com.pkg.e"]
        row.internal_state = STATE_ONLINE_CONFIRMED
        row.last_gamejoinloadtime_at = time.time() - 10
        row.last_positive_online_evidence_at = time.time() - 10
        row.online_since = time.time() - 10
        with patch.object(mon, "_process_check", return_value=(False, [])):
            mon.evaluate_package("com.pkg.e")
            mon.evaluate_package("com.pkg.e")
        self.assertEqual(row.internal_state, STATE_DEAD)
        self.assertTrue(row.force_close_detected)


class RjnWatchdogTimeoutTests(unittest.TestCase):
    def test_launch_watchdog_timeout(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.f"], launch_watchdog_seconds=120.0)
        t0 = 3_000_000.0
        mon.begin_launch_watchdog("com.pkg.f")
        row = mon._states["com.pkg.f"]
        row.launch_started_at = t0
        row.watchdog_active = True
        with patch("agent.rjn_lifecycle_monitor.time.time", return_value=t0 + 130):
            with patch.object(mon, "_process_check", return_value=(True, ["6"])):
                ev = mon.evaluate_package("com.pkg.f")
        self.assertEqual(row.internal_state, STATE_FAILED)
        self.assertFalse(ev.is_online_confirmed)


class RjnLaunchingDeadTests(unittest.TestCase):
    def test_process_missing_during_launching_stays_launching(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.launch"])
        mon._uid_map = {"com.pkg.launch": "800"}
        mon._uid_to_package = {"800": "com.pkg.launch"}
        mon.begin_launch_watchdog("com.pkg.launch")
        self.assertEqual(mon._states["com.pkg.launch"].internal_state, STATE_LAUNCHING)
        with patch.object(mon, "_process_check", return_value=(False, [])):
            mon.evaluate_package("com.pkg.launch")
            ev = mon.evaluate_package("com.pkg.launch")
        self.assertEqual(ev.internal_state, STATE_LAUNCHING)
        self.assertFalse(ev.is_online_confirmed)

    def test_launch_online_fallback_before_watchdog_timeout(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.g"], launch_watchdog_seconds=120.0)
        t0 = 4_000_000.0
        mon.begin_launch_watchdog("com.pkg.g")
        row = mon._states["com.pkg.g"]
        row.launch_started_at = t0
        row.watchdog_active = True
        def _confirm(_pkg: str, _now: float) -> bool:
            mon._confirm_online_evidence(_pkg, _now, source="activity_in_game")
            return True

        with patch("agent.rjn_lifecycle_monitor.time.time", return_value=t0 + 20), \
             patch.object(mon, "_process_check", return_value=(True, ["7"])), \
             patch.object(mon, "_try_confirm_launch_online", side_effect=_confirm) as confirm:
            ev = mon.evaluate_package("com.pkg.g")
        confirm.assert_called_once()
        self.assertTrue(ev.is_online_confirmed)
        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED)

    def test_weak_online_confirmation_deferred_before_min_age(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.h"])
        t0 = 5_000_000.0
        mon.begin_launch_watchdog("com.pkg.h")
        row = mon._states["com.pkg.h"]
        row.launch_started_at = t0
        with patch("agent.rjn_lifecycle_monitor.time.time", return_value=t0 + 5):
            mon._confirm_online_evidence("com.pkg.h", t0 + 5, source="logcat_join_hint")
        self.assertNotEqual(row.internal_state, STATE_ONLINE_CONFIRMED)
        with patch("agent.rjn_lifecycle_monitor.time.time", return_value=t0 + 21):
            mon._confirm_online_evidence("com.pkg.h", t0 + 21, source="logcat_join_hint")
        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED)


class RjnRecentsNotOnlineTests(unittest.TestCase):
    def test_process_without_gamejoin_not_online(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.g"])
        mon.begin_launch_watchdog("com.pkg.g")
        with patch.object(mon, "_process_check", return_value=(True, ["7"])):
            ev = mon.evaluate_package("com.pkg.g")
        self.assertFalse(ev.is_online_confirmed)
        self.assertIn("no_positive_online_evidence", ev.failed_checks)


if __name__ == "__main__":
    unittest.main()
