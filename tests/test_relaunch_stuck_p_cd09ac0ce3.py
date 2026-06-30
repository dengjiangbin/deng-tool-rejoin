"""Regression for probe p-cd09ac0ce3: post-grace relaunch must not pin Relaunching."""

from __future__ import annotations

import inspect
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.supervisor import (
    STATUS_DEAD,
    STATUS_FAILED,
    STATUS_JOIN_FAILED,
    STATUS_LAUNCHING,
    STATUS_ONLINE,
    STATUS_RELAUNCHING,
    WatchdogSupervisor,
)

_PKG = "com.moons.litesc"


def _entry() -> dict:
    return {"package": _PKG, "enabled": True, "account_username": "TestUser"}


def _cfg() -> dict:
    return {"supervisor": {"health_check_interval_seconds": 10}}


class RelaunchStuckRegressionTests(unittest.TestCase):
    def test_detect_source_unpins_relaunch_after_grace(self) -> None:
        from agent import supervisor as sup_mod

        src = inspect.getsource(sup_mod.WatchdogSupervisor._detect_android_package_state)
        self.assertIn("relaunch_post_grace_pending_confirmation", src)
        self.assertNotIn("relaunch_pending_gamejoinloadtime", src)

    def test_failed_relaunch_sets_failed_status(self) -> None:
        from agent import supervisor as sup_mod

        src = inspect.getsource(sup_mod.WatchdogSupervisor._handle_state)
        self.assertIn("self._set_status(pkg, STATUS_FAILED)", src)

    def test_post_grace_relaunch_advances_to_launching(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        sup._all_launches_completed = True
        sup._package_opened.add(_PKG)
        sup._last_launched_at[_PKG] = time.monotonic() - (sup.LOADING_GRACE_SECONDS + 10)
        sup._relaunch_inflight.add(_PKG)
        sup._relaunch_verify_until[_PKG] = time.monotonic() - 1.0
        sup.status_map[_PKG] = STATUS_RELAUNCHING

        ev = MagicMock()
        ev.is_online_confirmed = False
        ev.process_exists = True
        ev.internal_state = "RELAUNCHING"
        ev.reason = "no positive online evidence after launch"
        ev.detail = {
            "reason": "no positive online evidence after launch",
            "launch_failed_reason": "",
            "process_running": "true",
        }
        ev.failed_checks = []

        with patch.object(sup, "_ingest_push_heartbeat"), patch.object(
            sup, "_rjn_monitor"
        ) as mon:
            mon.evaluate_package.return_value = ev
            mon.try_mark_force_close_dead.return_value = False
            state, _ = sup._detect_android_package_state(_PKG)

        self.assertEqual(state, STATUS_LAUNCHING)
        self.assertNotIn(_PKG, sup._relaunch_inflight)

    def test_post_grace_relaunch_process_gone_is_dead(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        sup._all_launches_completed = True
        sup._package_opened.add(_PKG)
        sup._last_launched_at[_PKG] = time.monotonic() - (sup.LOADING_GRACE_SECONDS + 10)
        sup.status_map[_PKG] = STATUS_RELAUNCHING

        ev = MagicMock()
        ev.is_online_confirmed = False
        ev.process_exists = False
        ev.internal_state = "DEAD"
        ev.reason = "process_missing"
        ev.detail = {
            "reason": "process_missing",
            "reason_internal": "process_missing",
            "launch_failed_reason": "",
        }
        ev.failed_checks = []

        with patch.object(sup, "_ingest_push_heartbeat"), patch.object(
            sup, "_rjn_monitor"
        ) as mon:
            mon.evaluate_package.return_value = ev
            mon.try_mark_force_close_dead.return_value = False
            state, _ = sup._detect_android_package_state(_PKG)

        self.assertEqual(state, STATUS_DEAD)

    def test_handle_state_failed_launch_sets_failed(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        with patch.object(sup, "_reserve_recovery_launch_attempt", return_value=True), patch.object(
            sup, "run_recovery_cache_clear", create=True
        ), patch(
            "agent.cache_clear_phases.run_recovery_cache_clear",
            return_value={"success": True, "method": "test"},
        ), patch.object(sup, "_do_launch", return_value=False), patch.object(
            sup, "_mark_launched"
        ), patch(
            "agent.supervisor.launch_package_for_current_config"
        ):
            sup._handle_state(
                _PKG,
                _entry(),
                STATUS_DEAD,
                STATUS_ONLINE,
                time.time(),
            )
        self.assertEqual(sup.status_map.get(_PKG), STATUS_FAILED)


if __name__ == "__main__":
    unittest.main()
