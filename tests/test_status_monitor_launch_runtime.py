"""Status Monitor runtime must start from gamejoinloadtime only."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import webhook
from agent.commands import format_runtime_compact
from agent.rjn_lifecycle_monitor import RjnLifecycleMonitor
from agent.status_monitor_runtime import (
    clear_online_since,
    load_online_since,
    mark_online_confirmed_gamejoin,
)
from agent.supervisor import (
    STATUS_DEAD,
    STATUS_LAUNCHING,
    STATUS_ONLINE,
    STATUS_PENDING,
    STATUS_RELAUNCHING,
    WatchdogSupervisor,
)

PKG_A = "com.moons.litesc"
ENTRY_A = {"package": PKG_A, "enabled": True, "roblox_user_id": 12345, "account_username": "userA"}


class StatusMonitorGamejoinRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._state_path = webhook.DATA_DIR / "status-monitor-runtime-state.json"
        self._backup = (
            self._state_path.read_text(encoding="utf-8")
            if self._state_path.is_file()
            else None
        )
        self._state_path.unlink(missing_ok=True)

    def tearDown(self) -> None:
        self._state_path.unlink(missing_ok=True)
        if self._backup is not None:
            self._state_path.write_text(self._backup, encoding="utf-8")

    def _cfg(self, extra: dict | None = None) -> dict:
        base = {
            "webhook_mode": "new_post",
            "webhook_enabled": True,
            "roblox_packages": [{"package": PKG_A, "account_username": "userA"}],
        }
        if extra:
            base.update(extra)
        return base

    def test_runtime_not_started_until_gamejoinloadtime(self) -> None:
        t0 = 1_000_000.0
        supervisor = WatchdogSupervisor(
            [ENTRY_A],
            {"monitor_started_at": t0},
            initial_status={PKG_A: STATUS_PENDING},
        )
        with patch("time.time", return_value=t0):
            supervisor._set_status(PKG_A, STATUS_LAUNCHING)
        started_at, source = supervisor._status_monitor_runtime_started_at(PKG_A, STATUS_LAUNCHING)
        self.assertIsNone(started_at)
        self.assertEqual(source, "missing")

        join_at = t0 + 30.0
        mark_online_confirmed_gamejoin(PKG_A, join_at, previous_state="LAUNCHING")
        supervisor._set_status(PKG_A, STATUS_ONLINE)
        supervisor._record_runtime_session_state(PKG_A, STATUS_LAUNCHING, STATUS_ONLINE, join_at)
        started_at, source = supervisor._status_monitor_runtime_started_at(PKG_A, STATUS_ONLINE)
        self.assertEqual(started_at, join_at)
        self.assertEqual(source, "gamejoinloadtime")

    def test_relaunch_runtime_from_new_gamejoin(self) -> None:
        supervisor = WatchdogSupervisor([ENTRY_A], {})
        mark_online_confirmed_gamejoin(PKG_A, 100.0, previous_state="LAUNCHING")
        supervisor._record_runtime_session_state(PKG_A, STATUS_LAUNCHING, STATUS_ONLINE, 100.0)
        supervisor._record_runtime_session_state(PKG_A, STATUS_ONLINE, STATUS_DEAD, 700.0)
        mark_online_confirmed_gamejoin(PKG_A, 800.0, previous_state="RELAUNCHING")
        supervisor._record_runtime_session_state(PKG_A, STATUS_RELAUNCHING, STATUS_ONLINE, 800.0)
        started_at, source = supervisor._status_monitor_runtime_started_at(PKG_A, STATUS_ONLINE)
        self.assertEqual(started_at, 800.0)
        self.assertEqual(source, "gamejoinloadtime")
        self.assertEqual(format_runtime_compact(803.0 - started_at), "3s")

    def test_rjn_evaluate_online_requires_gamejoin(self) -> None:
        mon = RjnLifecycleMonitor([PKG_A])
        mon._uid_map = {PKG_A: "10101"}
        mon.begin_launch_watchdog(PKG_A)
        with patch.object(mon, "_process_check", return_value=(True, ["42"])):
            ev = mon.evaluate_package(PKG_A)
        self.assertFalse(ev.is_online_confirmed)
        mon._apply_phrase(
            PKG_A,
            "gamejoinloadtime",
            1_000_050.0,
            type("E", (), {"action_taken": ""})(),
        )
        with patch.object(mon, "_process_check", return_value=(True, ["42"])):
            ev2 = mon.evaluate_package(PKG_A)
        self.assertTrue(ev2.is_online_confirmed)


if __name__ == "__main__":
    unittest.main()
