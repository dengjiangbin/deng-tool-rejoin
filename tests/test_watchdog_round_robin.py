"""Round-robin watchdog pacing and Termux-safe force-stop regression tests."""

from __future__ import annotations

import inspect
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import android
from agent.supervisor import (
    STATUS_CHECKING,
    STATUS_DEAD,
    STATUS_LAUNCHING,
    STATUS_NO_HEARTBEAT,
    STATUS_ONLINE,
    STATUS_PENDING,
    WatchdogSupervisor,
)

_PKG = "com.roblox.client"
_PKG2 = "com.roblox.client2"


def _entry(pkg: str = _PKG) -> dict:
    return {
        "package": pkg,
        "account_username": "TestUser",
        "roblox_user_id": 12345,
        "enabled": True,
    }


def _cfg() -> dict:
    return {"supervisor": {}, "log_level": "INFO"}


def _alive_evidence() -> dict:
    return {
        "alive": True,
        "running": True,
        "root_running": False,
        "task": True,
        "window": True,
        "surface": False,
        "foreground": True,
        "process_missing": False,
    }


class TestRoundRobinWatchdog(unittest.TestCase):
    def test_package_round_robin_constant_is_three_seconds(self) -> None:
        self.assertEqual(WatchdogSupervisor.PACKAGE_ROUND_ROBIN_SECONDS, 3)

    def test_checking_hold_and_tail_constants(self) -> None:
        self.assertEqual(WatchdogSupervisor.PACKAGE_CHECKING_HOLD_SECONDS, 1.0)
        self.assertEqual(WatchdogSupervisor.PACKAGE_ROUND_ROBIN_TAIL_SECONDS, 2.0)

    def test_watchdog_loop_source_uses_round_robin_pause(self) -> None:
        src = inspect.getsource(WatchdogSupervisor._run_watchdog_loop)
        self.assertIn("PACKAGE_CHECKING_HOLD_SECONDS", src)
        self.assertIn("PACKAGE_ROUND_ROBIN_TAIL_SECONDS", src)
        self.assertIn("_interruptible_sleep", src)
        self.assertIn("WATCHDOG_ROUND_ROBIN_PAUSE", src)

    def test_dashboard_render_interval_is_one_second(self) -> None:
        self.assertEqual(WatchdogSupervisor.DASHBOARD_RENDER_INTERVAL_SECONDS, 1.0)
        self.assertEqual(
            WatchdogSupervisor.DASHBOARD_RENDER_INTERVAL_SECONDS,
            WatchdogSupervisor.PACKAGE_CHECKING_HOLD_SECONDS,
        )

    def test_watchdog_loop_source_uses_launch_latch(self) -> None:
        src = inspect.getsource(WatchdogSupervisor._run_watchdog_loop)
        self.assertIn("_all_launches_completed", src)
        self.assertIn("WATCHDOG_LAUNCH_LATCH", src)

    def test_watchdog_idles_until_launch_latch_released(self) -> None:
        sup = WatchdogSupervisor(
            [_entry(_PKG), _entry(_PKG2)],
            _cfg(),
            initial_status={_PKG: STATUS_PENDING, _PKG2: STATUS_LAUNCHING},
        )
        detect_calls: list[str] = []

        def _track_detect(pkg, entry, **kwargs):
            detect_calls.append(pkg)
            return (STATUS_ONLINE, {"reason": "mock"})

        with patch.object(sup, "_detect_package_state", side_effect=_track_detect), \
             patch.object(sup, "_evaluate_launching_or_pending", side_effect=_track_detect), \
             patch("agent.supervisor.db.insert_event"), \
             patch("agent.supervisor.db.insert_heartbeat"), \
             patch("agent.supervisor.log_event"):
            sup.start_daemon(display_interval=0.05)
            time.sleep(0.15)
            self.assertEqual(
                detect_calls, [],
                "watchdog must not check any package before launch latch releases",
            )
            sup.mark_all_launches_completed()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not detect_calls:
                time.sleep(0.05)
            sup.stop("test")
            if sup._watchdog_thread is not None:
                sup._watchdog_thread.join(timeout=3.0)

        self.assertTrue(detect_calls, "watchdog must check packages after latch release")

    def test_sequential_packages_sleep_hold_then_tail(self) -> None:
        sup = WatchdogSupervisor(
            [_entry(_PKG), _entry(_PKG2)],
            _cfg(),
            initial_status={_PKG: STATUS_LAUNCHING, _PKG2: STATUS_LAUNCHING},
        )
        sup.mark_all_launches_completed()
        sleep_calls: list[float] = []
        presence = MagicMock()
        presence.is_in_game = True
        presence.is_offline = False
        presence.is_lobby = False
        presence.is_unknown = False

        def _record_sleep(seconds: float) -> None:
            sleep_calls.append(float(seconds))

        with patch.object(sup, "_interruptible_sleep", side_effect=_record_sleep), \
             patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch.object(sup, "_fetch_presence", return_value=presence), \
             patch("agent.supervisor.db.insert_event"), \
             patch("agent.supervisor.db.insert_heartbeat"), \
             patch("agent.supervisor.log_event"):
            sup.start_daemon(display_interval=0.05)

            def _stop_soon() -> None:
                time.sleep(0.15)
                sup.stop("test")

            threading.Thread(target=_stop_soon, daemon=True).start()
            if sup._watchdog_thread is not None:
                sup._watchdog_thread.join(timeout=5.0)

        self.assertIn(1.0, sleep_calls, f"expected 1.0s Checking hold, got {sleep_calls}")
        self.assertIn(2.0, sleep_calls, f"expected 2.0s tail pause, got {sleep_calls}")

    def test_launching_package_sets_checking_before_eval(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_LAUNCHING})
        seen: list[str] = []
        original_set = sup._set_status

        def _track(pkg: str, status: str) -> None:
            seen.append(status)
            original_set(pkg, status)

        with patch.object(sup, "_set_status", side_effect=_track), \
             patch.object(
                 sup,
                 "_detect_package_state",
                 return_value=(STATUS_ONLINE, {"reason": "roblox_presence_in_game"}),
             ):
            sup._evaluate_launching_or_pending(_PKG, _entry())
        self.assertIn(STATUS_CHECKING, seen)


class TestCsrfPresenceHandshake(unittest.TestCase):
    def test_post_json_retries_with_csrf_token_on_403(self) -> None:
        import agent.roblox_presence as rp

        calls: list[dict[str, str]] = []

        def _fake_once(url, body, *, headers, timeout):
            calls.append(dict(headers))
            if "X-CSRF-TOKEN" not in headers:
                return (
                    403,
                    {"x-csrf-token": "csrf-token-abc"},
                    None,
                )
            return (
                200,
                {},
                {
                    "userPresences": [
                        {
                            "userId": 12345,
                            "userPresenceType": 2,
                            "placeId": 999,
                            "rootPlaceId": 888,
                            "lastLocation": "Game",
                            "lastOnline": "2026-01-01T00:00:00Z",
                        }
                    ]
                },
            )

        with patch.object(rp, "_roblox_post_once", side_effect=_fake_once):
            payload = rp._post_json(
                rp._PRESENCE_URL,
                {"userIds": [12345]},
                cookie="fake-cookie-value-long-enough",
            )

        self.assertIsNotNone(payload)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("X-CSRF-TOKEN", calls[0])
        self.assertEqual(calls[1].get("X-CSRF-TOKEN"), "csrf-token-abc")

    def test_presence_type_2_maps_to_in_game(self) -> None:
        import agent.roblox_presence as rp

        row = {
            "userId": 12345,
            "userPresenceType": 2,
            "placeId": 111,
            "rootPlaceId": 222,
            "lastLocation": "Live Game",
            "lastOnline": "2026-01-01T00:00:00Z",
        }
        parsed = rp._parse_presence_row(row)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertTrue(parsed.is_in_game)


class TestJoiningStateRemoved(unittest.TestCase):
    def test_supervisor_has_no_joining_constant(self) -> None:
        import agent.supervisor as sup_mod

        self.assertFalse(hasattr(sup_mod, "STATUS_JOINING"))

    def test_set_status_maps_joining_to_launching(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        sup._set_status(_PKG, "Joining")
        self.assertEqual(sup.status_map[_PKG], STATUS_LAUNCHING)

    def test_commands_start_has_no_joining_phase(self) -> None:
        import agent.commands as cmd

        src = inspect.getsource(cmd.cmd_start)
        self.assertNotIn('phase[package] = "Joining"', src)
        self.assertNotIn("_STATUS_JOINING", src)


class TestTermuxSafeForceStop(unittest.TestCase):
    def test_termux_is_force_stop_protected(self) -> None:
        self.assertTrue(android._is_force_stop_protected("com.termux"))

    def test_force_stop_package_skips_termux(self) -> None:
        with patch("agent.android.run_root_command") as mock_run:
            result = android.force_stop_package("com.termux")
        mock_run.assert_not_called()
        self.assertFalse(result.ok)

    def test_watchdog_kill_switch_uses_targeted_force_stop(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        sup._last_launched_at[_PKG] = time.monotonic() - (sup.LOADING_GRACE_SECONDS + 10)
        sup._nhb_since[_PKG] = time.monotonic() - (sup.NHB_KILL_SWITCH_SECONDS + 5)
        with patch.object(sup, "_force_stop_target_package", return_value=True) as mock_kill, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"), \
             patch("time.sleep"):
            sup._handle_state(_PKG, _entry(), "No Heartbeat", "Online", time.time())
        mock_kill.assert_called_once_with(_PKG)
        self.assertEqual(sup.status_map.get(_PKG), "Dead")

    def test_force_stop_target_blocks_termux(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        sup.packages = ["com.termux"]
        with patch("agent.android.force_stop_package") as mock_stop:
            ok = sup._force_stop_target_package("com.termux")
        self.assertFalse(ok)
        mock_stop.assert_not_called()


class TestLoadingGracePeriod(unittest.TestCase):
    def test_loading_grace_constant_is_30_seconds(self) -> None:
        self.assertEqual(WatchdogSupervisor.LOADING_GRACE_SECONDS, 30)

    def test_nhb_kill_switch_blocked_during_loading_grace(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        sup._last_launched_at[_PKG] = time.monotonic() - (
            sup.LOADING_GRACE_SECONDS / 2
        )
        sup._nhb_since[_PKG] = time.monotonic() - (sup.NHB_KILL_SWITCH_SECONDS + 5)
        with patch.object(sup, "_force_stop_target_package", return_value=True) as mock_kill, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"), \
             patch("time.sleep"):
            sup._handle_state(
                _PKG, _entry(), STATUS_NO_HEARTBEAT, STATUS_LAUNCHING, time.time()
            )
        mock_kill.assert_not_called()
        self.assertNotIn(_PKG, sup._nhb_since)

    def test_nhb_kill_switch_runs_after_loading_grace_expires(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        sup._last_launched_at[_PKG] = time.monotonic() - (sup.LOADING_GRACE_SECONDS + 10)
        sup._nhb_since[_PKG] = time.monotonic() - (sup.NHB_KILL_SWITCH_SECONDS + 5)
        with patch.object(sup, "_force_stop_target_package", return_value=True) as mock_kill, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"), \
             patch("time.sleep"):
            sup._handle_state(
                _PKG, _entry(), STATUS_NO_HEARTBEAT, STATUS_LAUNCHING, time.time()
            )
        mock_kill.assert_called_once_with(_PKG)

    def test_online_clears_nhb_tracking(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        sup._nhb_since[_PKG] = time.monotonic() - 30.0
        with patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"), \
             patch.object(sup, "_check_ram_optimization"):
            sup._handle_state(_PKG, _entry(), STATUS_ONLINE, STATUS_NO_HEARTBEAT, time.time())
        self.assertNotIn(_PKG, sup._nhb_since)


class TestCookieOnlyDetection(unittest.TestCase):
    def test_root_ps_miss_skips_presence(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_LAUNCHING})
        sup._root_info = MagicMock(available=True, tool="su")
        result = MagicMock(ok=False, stdout="")
        with patch("agent.android.run_root_command", return_value=result), \
             patch.object(sup, "_fetch_presence", side_effect=AssertionError("presence must be skipped")):
            state, detail = sup._detect_package_state(_PKG, _entry())
        self.assertEqual(state, STATUS_NO_HEARTBEAT)
        self.assertEqual(detail["reason"], "root_ps_missing")

    def test_recovery_launches_throttle_after_three_attempts(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        with patch("agent.supervisor.time.monotonic", return_value=100.0), \
             patch("agent.supervisor.log_event"):
            self.assertTrue(sup._reserve_recovery_launch_attempt(_PKG))
            self.assertTrue(sup._reserve_recovery_launch_attempt(_PKG))
            self.assertTrue(sup._reserve_recovery_launch_attempt(_PKG))
            self.assertFalse(sup._reserve_recovery_launch_attempt(_PKG))
        self.assertEqual(sup._recovery_throttle_until[_PKG], 160.0)
        with patch("agent.supervisor.time.monotonic", return_value=160.0), \
             patch("agent.supervisor.log_event"):
            self.assertTrue(sup._reserve_recovery_launch_attempt(_PKG))

    def test_recovery_gate_does_not_relaunch_while_waiting(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: "Waiting"})
        with patch.object(sup, "_evaluate_package_presence_isolated", return_value="Waiting"), \
             patch.object(sup, "_deploy_gate_recovery_cycle") as cycle, \
             patch.object(sup, "_interruptible_sleep", side_effect=lambda _seconds: sup.stop_event.set()), \
             patch("agent.db.insert_event"):
            sup._run_blocking_recovery_gate(
                _PKG, _entry(), package_index=1, package_total=1,
            )
        cycle.assert_not_called()

    def test_offline_presence_after_grace_is_no_heartbeat(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_LAUNCHING})
        sup._last_launched_at[_PKG] = time.monotonic() - (sup.LOADING_GRACE_SECONDS + 5)
        presence = MagicMock()
        presence.is_in_game = False
        presence.is_offline = True
        presence.is_lobby = False
        presence.is_unknown = False
        with patch.object(sup, "_fetch_presence", return_value=presence):
            state, detail = sup._detect_package_state(_PKG, _entry())
        self.assertEqual(state, STATUS_NO_HEARTBEAT)
        self.assertEqual(detail["reason"], "presence_offline")

    def test_offline_presence_during_loading_grace_stays_waiting(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_LAUNCHING})
        sup.mark_package_launched(_PKG)
        presence = MagicMock()
        presence.is_in_game = False
        presence.is_offline = True
        presence.is_lobby = False
        presence.is_unknown = False
        with patch.object(sup, "_fetch_presence", return_value=presence):
            state, detail = sup._detect_package_state(_PKG, _entry())
        self.assertEqual(state, "Waiting")
        self.assertEqual(detail["reason"], "presence_checked_loading_grace")


class TestLaunchTimestampBinding(unittest.TestCase):
    def test_mark_package_launched_writes_monotonic_timestamp(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        before = time.monotonic()
        sup.mark_package_launched(_PKG)
        after = time.monotonic()
        ts = sup._last_launched_at[_PKG]
        self.assertGreaterEqual(ts, before)
        self.assertLessEqual(ts, after)
        self.assertEqual(sup.status_map[_PKG], STATUS_LAUNCHING)

    def test_missing_launch_timestamp_defaults_to_fresh_grace(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_LAUNCHING})
        self.assertNotIn(_PKG, sup._last_launched_at)
        self.assertTrue(sup._in_loading_grace(_PKG))
        self.assertIn(_PKG, sup._last_launched_at)

    def test_mark_all_launches_completed_backfills_missing_timestamps(self) -> None:
        sup = WatchdogSupervisor(
            [_entry(_PKG), _entry(_PKG2)],
            _cfg(),
            initial_status={_PKG: STATUS_LAUNCHING, _PKG2: STATUS_PENDING},
        )
        sup.mark_package_launched(_PKG)
        sup.mark_all_launches_completed()
        self.assertIn(_PKG, sup._last_launched_at)
        self.assertIn(_PKG2, sup._last_launched_at)

    def test_nhb_kill_switch_blocked_when_launch_timestamp_missing(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        sup._nhb_since[_PKG] = time.monotonic() - (sup.NHB_KILL_SWITCH_SECONDS + 5)
        with patch.object(sup, "_force_stop_target_package", return_value=True) as mock_kill, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"), \
             patch("time.sleep"):
            sup._handle_state(
                _PKG, _entry(), STATUS_NO_HEARTBEAT, STATUS_LAUNCHING, time.time()
            )
        mock_kill.assert_not_called()
        self.assertNotIn(_PKG, sup._nhb_since)


class TestBlockingRecoveryGate(unittest.TestCase):
    def test_recovery_gate_poll_constant_is_5_seconds(self) -> None:
        self.assertEqual(WatchdogSupervisor.RECOVERY_GATE_POLL_SECONDS, 5.0)

    def test_watchdog_loop_source_uses_blocking_recovery_gate(self) -> None:
        loop_src = inspect.getsource(WatchdogSupervisor._run_watchdog_loop)
        gate_src = inspect.getsource(WatchdogSupervisor._run_blocking_recovery_gate)
        self.assertIn("_run_blocking_recovery_gate", loop_src)
        self.assertIn("RECOVERY_GATE", gate_src)

    def test_recovery_gate_halts_round_robin_until_online(self) -> None:
        sup = WatchdogSupervisor(
            [_entry(_PKG), _entry(_PKG2)],
            _cfg(),
            initial_status={_PKG: STATUS_ONLINE, _PKG2: STATUS_ONLINE},
        )
        sup.mark_all_launches_completed()
        events: list[tuple[str, str]] = []
        gate_polls = {"n": 0}

        def _detect(pkg, entry, **kwargs):
            events.append(("detect", pkg))
            if pkg == _PKG and sum(1 for kind, p in events if kind == "detect" and p == _PKG) == 1:
                return (STATUS_DEAD, {"reason": "process_missing"})
            return (STATUS_ONLINE, {"reason": "mock_online"})

        def _isolated(pkg, entry):
            events.append(("gate", pkg))
            gate_polls["n"] += 1
            return STATUS_ONLINE if gate_polls["n"] >= 1 else STATUS_LAUNCHING

        with patch.object(sup, "_detect_package_state", side_effect=_detect), \
             patch.object(sup, "_evaluate_package_presence_isolated", side_effect=_isolated), \
             patch.object(sup, "_do_launch", return_value=True), \
             patch.object(sup, "_interruptible_sleep"), \
             patch("agent.supervisor.db.insert_event"), \
             patch("agent.supervisor.db.insert_heartbeat"), \
             patch("agent.supervisor.log_event"):
            sup.start_daemon(display_interval=0.05)

            def _stop_soon() -> None:
                time.sleep(0.25)
                sup.stop("test")

            threading.Thread(target=_stop_soon, daemon=True).start()
            if sup._watchdog_thread is not None:
                sup._watchdog_thread.join(timeout=5.0)

        pkg2_detect_idx = next(
            (i for i, ev in enumerate(events) if ev == ("detect", _PKG2)),
            None,
        )
        first_gate_idx = next(
            (i for i, ev in enumerate(events) if ev[0] == "gate"),
            None,
        )
        self.assertIsNotNone(first_gate_idx, f"expected recovery gate, events={events}")
        self.assertGreater(gate_polls["n"], 0)
        if pkg2_detect_idx is not None and first_gate_idx is not None:
            self.assertGreater(
                pkg2_detect_idx,
                first_gate_idx,
                f"package 2 must not be checked before recovery gate completes: {events}",
            )

    def test_recovery_gate_exits_on_dead(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_ONLINE})
        with patch.object(sup, "_evaluate_package_presence_isolated", return_value=STATUS_DEAD), \
             patch.object(sup, "_interruptible_sleep") as mock_sleep, \
             patch("agent.supervisor.log_event"):
            sup._run_blocking_recovery_gate(
                _PKG,
                _entry(),
                package_index=1,
                package_total=1,
            )
        mock_sleep.assert_not_called()
        self.assertEqual(sup.status_map.get(_PKG), STATUS_DEAD)


if __name__ == "__main__":
    unittest.main()
