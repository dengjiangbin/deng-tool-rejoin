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
    UidResolution,
)
from agent.android_logcat_detector import LogcatPackageEvent
from agent.supervisor import (
    WatchdogSupervisor,
    STATUS_DEAD,
    STATUS_DISCONNECTED,
    STATUS_JOIN_FAILED,
    STATUS_LAUNCHING,
    STATUS_ONLINE,
    STATUS_PENDING,
    STATUS_READY,
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
        sup._package_opened.add("com.pkg.b")
        sup._rjn_monitor._uid_map = {"com.pkg.b": "200"}
        sup._rjn_monitor._uid_to_package = {"200": "com.pkg.b"}
        t0 = time.time() - 25.0
        sup._rjn_monitor.begin_launch_watchdog("com.pkg.b")
        sup._rjn_monitor._states["com.pkg.b"].launch_started_at = t0
        presence = MagicMock(is_in_game=True)
        with patch.object(sup, "_fetch_presence", return_value=presence), \
             patch.object(sup._rjn_monitor, "_process_check", return_value=(True, ["2"])):
            state, detail = sup._detect_android_package_state("com.pkg.b")
        self.assertEqual(state, STATUS_ONLINE)
        self.assertEqual(detail.get("online_confirmed"), "true")

    def test_launching_presence_fallback_waits_for_min_age(self) -> None:
        entry = {"package": "com.pkg.b2", "account_username": "u2"}
        sup = WatchdogSupervisor([entry], {"roblox_packages": [entry]})
        sup.status_map["com.pkg.b2"] = STATUS_LAUNCHING
        sup._package_opened.add("com.pkg.b2")
        sup._rjn_monitor.begin_launch_watchdog("com.pkg.b2")
        presence = MagicMock(is_in_game=True)
        with patch.object(sup, "_fetch_presence", return_value=presence), \
             patch.object(sup._rjn_monitor, "_process_check", return_value=(True, ["2"])):
            state, detail = sup._detect_android_package_state("com.pkg.b2")
        self.assertEqual(state, STATUS_LAUNCHING)
        self.assertEqual(detail.get("online_confirmed"), "false")


class LaunchingDeadTests(unittest.TestCase):
    def test_launching_process_missing_stays_launching(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.c"])
        mon._uid_map = {"com.pkg.c": "300"}
        mon._uid_to_package = {"300": "com.pkg.c"}
        mon.begin_launch_watchdog("com.pkg.c")
        with patch.object(mon, "_process_check", return_value=(False, [])):
            mon.evaluate_package("com.pkg.c")
            ev = mon.evaluate_package("com.pkg.c")
        self.assertEqual(ev.internal_state, STATE_LAUNCHING)

    def test_process_missing_after_online_is_dead(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.c2"])
        mon._uid_map = {"com.pkg.c2": "301"}
        mon._uid_to_package = {"301": "com.pkg.c2"}
        row = mon._states["com.pkg.c2"]
        row.internal_state = STATE_ONLINE_CONFIRMED
        row.last_positive_online_evidence_at = time.time() - 30
        with patch.object(mon, "_process_check", return_value=(False, [])):
            mon.evaluate_package("com.pkg.c2")
            ev = mon.evaluate_package("com.pkg.c2")
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
        sup._all_launches_completed = True
        with patch.object(sup, "_reserve_recovery_launch_attempt", return_value=True), \
             patch.object(sup, "_do_launch", return_value=True) as launch, \
             patch("agent.cache_clear_phases.run_recovery_cache_clear", return_value={"success": True}), \
             patch.object(sup._rjn_monitor, "note_launch_watchdog"):
            gate = sup._handle_state(
                "com.pkg.e",
                entry,
                STATUS_DEAD,
                STATUS_LAUNCHING,
                time.time(),
                detail={"reason_internal": "process_missing"},
            )
        # Non-blocking recovery (probe p-765bbcc3d3): the dead package is
        # relaunched once but _handle_state returns False so the watchdog
        # continues its round-robin to the next package instead of halting in a
        # blocking "Launching" gate.
        self.assertFalse(gate)
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
        sup._all_launches_completed = True
        with patch.object(sup, "_reserve_recovery_launch_attempt", return_value=True), \
             patch.object(sup, "_do_launch", return_value=True), \
             patch("agent.cache_clear_phases.run_recovery_cache_clear", return_value={"success": True}), \
             patch.object(sup._rjn_monitor, "note_launch_watchdog"):
            gate = sup._handle_state(
                "com.pkg.g",
                entry,
                STATUS_JOIN_FAILED,
                STATUS_LAUNCHING,
                time.time(),
                detail={"launch_failed_reason": "no_online_confirmation"},
            )
        # Non-blocking recovery (probe p-765bbcc3d3): relaunch dispatched once,
        # no blocking gate — _handle_state returns False so the round-robin
        # advances to the next package.
        self.assertFalse(gate)
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

    def test_relaunching_process_missing_stays_relaunching_until_online(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.j"])
        mon._uid_map = {"com.pkg.j": "910"}
        mon._uid_to_package = {"910": "com.pkg.j"}
        mon.note_launch_watchdog("com.pkg.j", relaunch=True)
        with patch.object(mon, "_process_check", return_value=(False, [])):
            mon.evaluate_package("com.pkg.j")
            ev = mon.evaluate_package("com.pkg.j")
        self.assertEqual(ev.internal_state, STATE_RELAUNCHING)

    def test_relaunching_process_missing_after_online_is_dead(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.j2"])
        mon._uid_map = {"com.pkg.j2": "911"}
        mon._uid_to_package = {"911": "com.pkg.j2"}
        mon.note_launch_watchdog("com.pkg.j2", relaunch=True)
        row = mon._states["com.pkg.j2"]
        row.last_positive_online_evidence_at = time.time() - 20
        with patch.object(mon, "_process_check", return_value=(False, [])):
            mon.evaluate_package("com.pkg.j2")
            ev = mon.evaluate_package("com.pkg.j2")
        self.assertEqual(ev.internal_state, STATE_DEAD)


class JoinFailedPresenceFallbackTests(unittest.TestCase):
    def test_join_failed_presence_confirms_online(self) -> None:
        entry = {"package": "com.pkg.h", "account_username": "user1"}
        sup = WatchdogSupervisor([entry], {"roblox_packages": [entry]})
        sup.status_map["com.pkg.h"] = STATUS_JOIN_FAILED
        sup._package_opened.add("com.pkg.h")
        mon = sup._rjn_monitor
        mon._uid_map = {"com.pkg.h": "800"}
        mon._uid_to_package = {"800": "com.pkg.h"}
        row = mon._states["com.pkg.h"]
        row.internal_state = STATE_FAILED
        row.launch_failed_reason = "no_online_confirmation"
        row.process_exists = True
        presence = MagicMock(is_in_game=True)
        with patch.object(mon, "_process_check", return_value=(True, ["8"])), \
             patch.object(sup, "_fetch_presence", return_value=presence):
            state, detail = sup._detect_android_package_state("com.pkg.h")
        self.assertEqual(state, STATUS_ONLINE)
        self.assertEqual(detail.get("online_confirmed"), "true")


class LogcatJoinHintTests(unittest.TestCase):
    def test_join_hint_line_confirms_online(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.k"])
        mon._uid_map = {"com.pkg.k": "700"}
        mon._uid_to_package = {"700": "com.pkg.k"}
        mon._monitor_started_at = 0.0
        mon._handle_logcat_line("uid=700 I Roblox: joingame success for place")
        row = mon._states["com.pkg.k"]
        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED)
        self.assertEqual(row.online_evidence_source, "logcat_join_hint")

    def test_package_name_fallback_without_uid(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.m"])
        mon._monitor_started_at = 0.0
        mon._handle_logcat_line("I Roblox com.pkg.m: gamejoinloadtime=1234")
        row = mon._states["com.pkg.m"]
        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED)
        self.assertEqual(row.online_evidence_source, "gamejoinloadtime")


class LogcatPollBackfillTests(unittest.TestCase):
    def test_poll_recent_logcat_confirms_join(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.n"])
        mon._uid_map = {"com.pkg.n": "710"}
        fake_event = LogcatPackageEvent(
            "com.pkg.n",
            "package_logcat_game_join_loaded",
            "gamejoinloadtime",
            time.time(),
        )
        with patch(
            "agent.android_logcat_detector.poll_logcat_events",
            return_value=([fake_event], object()),
        ):
            mon._poll_recent_logcat()
        self.assertEqual(mon._states["com.pkg.n"].internal_state, STATE_ONLINE_CONFIRMED)


class UidRefreshTests(unittest.TestCase):
    def test_uid_refresh_does_not_force_failed_state(self) -> None:
        mon = RjnLifecycleMonitor(["com.pkg.l"])
        row = mon._states["com.pkg.l"]
        row.internal_state = STATE_LAUNCHING
        with patch(
            "agent.rjn_lifecycle_monitor.resolve_package_uid",
            return_value=UidResolution(
                package="com.pkg.l",
                uid=None,
                error="userId not found",
            ),
        ):
            mon.refresh_uid_map()
        self.assertEqual(row.internal_state, STATE_LAUNCHING)
        self.assertEqual(row.uid_error, "userId not found")


class JoinFailedActivityFallbackTests(unittest.TestCase):
    def test_join_failed_activity_native_main_becomes_online(self) -> None:
        entry = {"package": "com.pkg.o", "account_username": ""}
        sup = WatchdogSupervisor([entry], {"roblox_packages": [entry]})
        sup.status_map["com.pkg.o"] = STATUS_JOIN_FAILED
        sup._package_opened.add("com.pkg.o")
        mon = sup._rjn_monitor
        mon._states["com.pkg.o"].internal_state = STATE_FAILED
        mon._states["com.pkg.o"].launch_failed_reason = "no_online_confirmation"
        fake_scan = MagicMock(
            pid_exists=True,
            has_resumed_or_top_for_package=True,
            in_game_evidence=True,
            home_or_lobby_only=False,
            disconnected_text_detected=False,
            logcat_disconnect_detected=False,
            recents_only=False,
            force_stopped=False,
        )
        fake_decision = MagicMock(
            is_online_confirmed=True,
            is_disconnected=False,
            reason="ok",
            failed_checks=[],
        )
        with patch.object(mon, "_process_check", return_value=(True, ["9"])), \
             patch.object(sup, "_fetch_presence", return_value=None), \
             patch("agent.package_online_evidence.collect_online_evidence", return_value=fake_scan), \
             patch("agent.package_online_evidence.evaluate_online_confirmed", return_value=fake_decision), \
             patch("agent.package_online_evidence.detect_live_disconnect", return_value=(None, None)):
            state, detail = sup._detect_android_package_state("com.pkg.o")
        self.assertEqual(state, STATUS_ONLINE)
        self.assertEqual(detail.get("online_confirmed"), "true")


class IdleDisconnectFallbackTests(unittest.TestCase):
    def test_online_fallback_blocked_when_idle_disconnect_detected(self) -> None:
        entry = {"package": "com.pkg.p", "account_username": ""}
        sup = WatchdogSupervisor([entry], {"roblox_packages": [entry]})
        sup.status_map["com.pkg.p"] = STATUS_JOIN_FAILED
        sup._package_opened.add("com.pkg.p")
        mon = sup._rjn_monitor
        mon._states["com.pkg.p"].internal_state = STATE_FAILED
        fake_scan = MagicMock(
            pid_exists=True,
            has_resumed_or_top_for_package=True,
            in_game_evidence=True,
            home_or_lobby_only=False,
            disconnected_text_detected=True,
            matched_disconnect_text="Error Code: 278",
            logcat_disconnect_detected=False,
            recents_only=False,
            force_stopped=False,
        )
        fake_decision = MagicMock(
            is_online_confirmed=False,
            is_disconnected=True,
            reason="disconnect detected",
            failed_checks=["disconnect_detected"],
        )
        with patch.object(mon, "_process_check", return_value=(True, ["10"])), \
             patch.object(sup, "_fetch_presence", return_value=None), \
             patch("agent.package_online_evidence.collect_online_evidence", return_value=fake_scan), \
             patch("agent.package_online_evidence.evaluate_online_confirmed", return_value=fake_decision), \
             patch("agent.package_online_evidence.detect_live_disconnect", return_value=("idle_disconnect_278", "Error Code: 278")):
            state, detail = sup._detect_android_package_state("com.pkg.p")
        self.assertEqual(state, STATUS_DISCONNECTED)
        self.assertEqual(detail.get("online_confirmed"), "false")


class PendingBootstrapTests(unittest.TestCase):
    def test_pending_initial_status_is_ready(self) -> None:
        entry = {"package": "com.pkg.q", "account_username": ""}
        sup = WatchdogSupervisor(
            [entry],
            {"roblox_packages": [entry]},
            initial_status={"com.pkg.q": STATUS_PENDING},
        )
        self.assertEqual(sup.status_map["com.pkg.q"], STATUS_READY)

    def test_unopened_package_reports_ready(self) -> None:
        entry = {"package": "com.pkg.q2", "account_username": ""}
        sup = WatchdogSupervisor([entry], {"roblox_packages": [entry]})
        state, detail = sup._detect_android_package_state("com.pkg.q2")
        self.assertEqual(state, STATUS_READY)
        self.assertEqual(detail.get("reason"), "awaiting_first_launch")

    def test_set_status_preserves_join_failed(self) -> None:
        entry = {"package": "com.pkg.r", "account_username": ""}
        sup = WatchdogSupervisor([entry], {"roblox_packages": [entry]})
        sup._set_status("com.pkg.r", STATUS_JOIN_FAILED)
        self.assertEqual(sup.status_map["com.pkg.r"], STATUS_JOIN_FAILED)


if __name__ == "__main__":
    unittest.main()
