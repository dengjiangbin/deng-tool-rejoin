"""State machine: online proof, dead from Launching, relaunch, webhook."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import webhook
from agent.constants import DATA_DIR
from agent.lifecycle_reasons import format_user_friendly_dead_reason
from agent.rjn_lifecycle_monitor import (
    RjnLifecycleMonitor,
    STATE_DEAD,
    STATE_FAILED,
    STATE_LAUNCHING,
    STATE_ONLINE_CONFIRMED,
    STATE_RELAUNCHING,
)
from agent.supervisor import (
    WatchdogSupervisor,
    STATUS_DEAD,
    STATUS_JOIN_FAILED,
    STATUS_LAUNCHING,
    STATUS_ONLINE,
    STATUS_RELAUNCHING,
)


class LaunchingOnlineTests(unittest.TestCase):
    def setUp(self) -> None:
        path = DATA_DIR / "status-monitor-runtime-state.json"
        path.unlink(missing_ok=True)
        path2 = DATA_DIR / "package-lifecycle-state.json"
        path2.unlink(missing_ok=True)

    def test_launching_gamejoinloadtime_becomes_online(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.a"])
        mon._uid_map = {"com.pkg.a": "100"}
        mon._uid_to_package = {"100": "com.pkg.a"}
        mon.begin_launch_watchdog("com.pkg.a")
        join_at = time.time()
        mon._confirm_online_evidence("com.pkg.a", join_at, source="gamejoinloadtime")
        with patch.object(mon, "_process_check", return_value=(True, ["1"])):
            ev = mon.evaluate_package("com.pkg.a")
        self.assertTrue(ev.is_online_confirmed)
        self.assertEqual(ev.public_status, "Online")

    def test_launching_presence_fallback_becomes_online(self) -> None:
        entry = {"package": "com.pkg.b", "account_username": "u1"}
        sup = WatchdogSupervisor([entry], {"roblox_packages": [entry]})
        sup.status_map["com.pkg.b"] = STATUS_LAUNCHING
        sup._rjn_monitor._uid_map = {"com.pkg.b": "200"}
        sup._rjn_monitor._uid_to_package = {"200": "com.pkg.b"}
        sup._rjn_monitor.begin_launch_watchdog("com.pkg.b")
        presence = MagicMock(is_in_game=True)
        with patch.object(sup, "_fetch_presence", return_value=presence), \
             patch.object(sup._rjn_monitor, "_process_check", return_value=(True, ["2"])):
            state, detail = sup._detect_android_package_state("com.pkg.b")
        self.assertEqual(state, STATUS_ONLINE)
        self.assertEqual(detail.get("online_confirmed"), "true")


class LaunchingDeadTests(unittest.TestCase):
    def test_launching_process_missing_becomes_dead(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.c"])
        mon._uid_map = {"com.pkg.c": "300"}
        mon._uid_to_package = {"300": "com.pkg.c"}
        mon.begin_launch_watchdog("com.pkg.c")
        with patch.object(mon, "_process_check", return_value=(False, [])):
            mon.evaluate_package("com.pkg.c")
            ev = mon.evaluate_package("com.pkg.c")
        self.assertEqual(ev.internal_state, STATE_DEAD)

    def test_launching_dead_webhook_allowed(self) -> None:
        entry = {"package": "com.pkg.d", "account_username": "user1"}
        sup = WatchdogSupervisor([entry], {"roblox_packages": [entry]})
        detail = {"reason_internal": "process_missing", "dead_reason": "process_missing"}
        allowed = sup._should_attempt_package_dead_webhook(
            "com.pkg.d",
            STATUS_LAUNCHING,
            STATUS_DEAD,
            time.time(),
            detail,
        )
        self.assertTrue(allowed)

    def test_launching_dead_triggers_relaunch_handle_state(self) -> None:
        entry = {"package": "com.pkg.e", "account_username": "user1"}
        sup = WatchdogSupervisor([entry], {"roblox_packages": [entry]})
        with patch.object(sup, "_reserve_recovery_launch_attempt", return_value=True), \
             patch.object(sup, "_do_launch", return_value=True) as launch, \
             patch("agent.android.clear_package_cache_verified", return_value={"success": True}), \
             patch.object(sup._rjn_monitor, "note_launch_watchdog"):
            gate = sup._handle_state(
                "com.pkg.e",
                entry,
                STATUS_DEAD,
                STATUS_LAUNCHING,
                time.time(),
                detail={"reason_internal": "process_missing"},
            )
        self.assertTrue(gate)
        launch.assert_called_once()
        self.assertEqual(sup.status_map.get("com.pkg.e"), STATUS_RELAUNCHING)


class WatchdogFailedTests(unittest.TestCase):
    def test_launching_watchdog_timeout_join_failed(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.f"], launch_watchdog_seconds=120.0)
        t0 = 4_000_000.0
        mon.begin_launch_watchdog("com.pkg.f")
        row = mon._states["com.pkg.f"]
        row.launch_started_at = t0
        row.watchdog_active = True
        with patch("agent.rjn_lifecycle_monitor.time.time", return_value=t0 + 130):
            with patch.object(mon, "_process_check", return_value=(True, ["6"])):
                ev = mon.evaluate_package("com.pkg.f")
        self.assertEqual(row.internal_state, STATE_FAILED)
        self.assertEqual(row.launch_failed_reason, "no_online_confirmation")

    def test_join_failed_recovery_path(self) -> None:
        entry = {"package": "com.pkg.g", "account_username": "user1"}
        sup = WatchdogSupervisor([entry], {"roblox_packages": [entry]})
        with patch.object(sup, "_reserve_recovery_launch_attempt", return_value=True), \
             patch.object(sup, "_do_launch", return_value=True), \
             patch("agent.android.clear_package_cache_verified", return_value={"success": True}), \
             patch.object(sup._rjn_monitor, "note_launch_watchdog"):
            gate = sup._handle_state(
                "com.pkg.g",
                entry,
                STATUS_JOIN_FAILED,
                STATUS_LAUNCHING,
                time.time(),
                detail={"launch_failed_reason": "no_online_confirmation"},
            )
        self.assertTrue(gate)
        self.assertEqual(sup.status_map.get("com.pkg.g"), STATUS_RELAUNCHING)


class WebhookEpisodeTests(unittest.TestCase):
    def test_webhook_reason_label(self) -> None:
        payload = webhook.build_package_lifecycle_embed_payload(
            {"device_name": "Phone"},
            event="package_dead",
            package="com.test.pkg",
            username="User1",
            dead_reason="launch_watchdog_timeout",
        )
        names = [f["name"] for f in payload["embeds"][0]["fields"]]
        self.assertIn("Reason", names)
        self.assertNotIn("Dead Reason", names)

    def test_watchdog_maps_user_friendly(self) -> None:
        text = format_user_friendly_dead_reason("launch_watchdog_timeout")
        self.assertIn("did not finish joining", text)

    def test_recovered_resets_dead_episode(self) -> None:
        webhook.mark_package_lifecycle_dead_notified("com.pkg.h", username="u")
        self.assertTrue(webhook.package_lifecycle_dead_already_notified("com.pkg.h"))
        webhook.mark_package_lifecycle_recovered("com.pkg.h", username="u")
        self.assertFalse(webhook.package_lifecycle_dead_already_notified("com.pkg.h"))


class RelaunchingTests(unittest.TestCase):
    def test_relaunching_gamejoin_becomes_online(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.i"])
        mon._uid_map = {"com.pkg.i": "900"}
        mon._uid_to_package = {"900": "com.pkg.i"}
        mon.note_launch_watchdog("com.pkg.i", relaunch=True)
        self.assertEqual(mon._states["com.pkg.i"].internal_state, STATE_RELAUNCHING)
        mon._confirm_online_evidence("com.pkg.i", time.time(), source="gamejoinloadtime")
        with patch.object(mon, "_process_check", return_value=(True, ["9"])):
            ev = mon.evaluate_package("com.pkg.i")
        self.assertTrue(ev.is_online_confirmed)

    def test_relaunching_process_missing_dead(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.j"])
        mon._uid_map = {"com.pkg.j": "910"}
        mon._uid_to_package = {"910": "com.pkg.j"}
        mon.note_launch_watchdog("com.pkg.j", relaunch=True)
        with patch.object(mon, "_process_check", return_value=(False, [])):
            mon.evaluate_package("com.pkg.j")
            ev = mon.evaluate_package("com.pkg.j")
        self.assertEqual(ev.internal_state, STATE_DEAD)


if __name__ == "__main__":
    unittest.main()
