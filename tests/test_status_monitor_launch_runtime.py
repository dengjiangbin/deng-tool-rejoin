"""Status Monitor runtime must start at package Launching, not monitor Start."""

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
from agent.status_monitor_runtime import (
    clear_package_launch_started,
    load_package_launch_started_at,
    persist_package_launch_started,
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
PKG_B = "com.moons.litesd"
ENTRY_A = {"package": PKG_A, "enabled": True, "roblox_user_id": 12345, "account_username": "userA"}
ENTRY_B = {"package": PKG_B, "enabled": True, "roblox_user_id": 12346, "account_username": "userB"}


class StatusMonitorLaunchRuntimeTests(unittest.TestCase):
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

    def _cfg(self, extra: dict | None = None, packages: list[dict] | None = None) -> dict:
        base = {
            "webhook_mode": "new_post",
            "webhook_enabled": True,
            "roblox_packages": packages or [{"package": PKG_A, "account_username": "userA"}],
        }
        if extra:
            base.update(extra)
        return base

    def _runtime_for_package(self, cfg: dict, snapshot: list[dict], package: str) -> str:
        payload = webhook.build_status_embed_payload(
            cfg,
            supervisor_snapshot=snapshot,
            app_stats={row["package"]: {"online": row.get("status") == "Online"} for row in snapshot},
        )
        detail = next(
            f["value"] for f in payload["embeds"][0]["fields"] if f["name"] == "Application Details"
        )
        marker = f"||{cfg['roblox_packages'][0]['account_username'] if package == PKG_A else 'userB'}||"
        for line in detail.splitlines():
            if marker in line and "⏱️" in line:
                return line.split("⏱️", 1)[1].strip()
        for line in detail.splitlines():
            if "⏱️" in line:
                return line.split("⏱️", 1)[1].strip()
        return ""

    def test_first_launch_runtime_uses_launching_not_monitor_start(self) -> None:
        t0 = 1_000_000.0
        launch_at = t0 + 30.0
        monitor_at = t0
        supervisor = WatchdogSupervisor(
            [ENTRY_A],
            {
                "monitor_started_at": monitor_at,
                "package_start_times": {PKG_A: "2001-01-01T00:00:00+00:00"},
            },
            initial_status={PKG_A: STATUS_PENDING},
        )
        with patch("time.time", return_value=launch_at):
            supervisor._set_status(PKG_A, STATUS_LAUNCHING)
        supervisor._set_status(PKG_A, STATUS_ONLINE)
        supervisor._record_runtime_session_state(PKG_A, STATUS_LAUNCHING, STATUS_ONLINE, launch_at + 30.0)

        snapshot = supervisor.get_status_snapshot()
        with patch("agent.webhook.time.time", return_value=t0 + 90.0):
            rendered = self._runtime_for_package(self._cfg(supervisor.cfg), snapshot, PKG_A)
        self.assertEqual(rendered, "1m 0s")
        self.assertEqual(snapshot[0]["runtime_source"], "package_launch_started_at")

    def test_delayed_launch_after_start_excludes_wait_time(self) -> None:
        t0 = 2_000_000.0
        launch_at = t0 + 300.0
        supervisor = WatchdogSupervisor([ENTRY_A], {"monitor_started_at": t0}, initial_status={PKG_A: STATUS_PENDING})
        with patch("time.time", return_value=launch_at):
            supervisor._set_status(PKG_A, STATUS_LAUNCHING)
        supervisor._set_status(PKG_A, STATUS_ONLINE)
        supervisor._record_runtime_session_state(PKG_A, STATUS_LAUNCHING, STATUS_ONLINE, launch_at + 30.0)
        snapshot = supervisor.get_status_snapshot()
        with patch("agent.webhook.time.time", return_value=t0 + 360.0):
            rendered = self._runtime_for_package(self._cfg(supervisor.cfg), snapshot, PKG_A)
        self.assertEqual(rendered, "1m 0s")

    def test_multiple_packages_keep_distinct_launch_times(self) -> None:
        t0 = 3_000_000.0
        supervisor = WatchdogSupervisor(
            [ENTRY_A, ENTRY_B],
            {"monitor_started_at": t0},
            initial_status={PKG_A: STATUS_PENDING, PKG_B: STATUS_PENDING},
        )
        with patch("time.time", return_value=t0):
            supervisor._set_status(PKG_A, STATUS_LAUNCHING)
        supervisor._set_status(PKG_A, STATUS_ONLINE)
        supervisor._record_runtime_session_state(PKG_A, STATUS_LAUNCHING, STATUS_ONLINE, t0 + 10.0)
        with patch("time.time", return_value=t0 + 180.0):
            supervisor._set_status(PKG_B, STATUS_LAUNCHING)
        supervisor._set_status(PKG_B, STATUS_ONLINE)
        supervisor._record_runtime_session_state(PKG_B, STATUS_LAUNCHING, STATUS_ONLINE, t0 + 200.0)
        snapshot = supervisor.get_status_snapshot()
        by_pkg = {row["package"]: row for row in snapshot}
        self.assertEqual(by_pkg[PKG_A]["runtime_source"], "package_launch_started_at")
        self.assertEqual(by_pkg[PKG_B]["runtime_source"], "package_launch_started_at")
        with patch("agent.webhook.time.time", return_value=t0 + 240.0):
            payload = webhook.build_status_embed_payload(
                self._cfg(
                    supervisor.cfg,
                    packages=[
                        {"package": PKG_A, "account_username": "userA"},
                        {"package": PKG_B, "account_username": "userB"},
                    ],
                ),
                supervisor_snapshot=snapshot,
                app_stats={
                    PKG_A: {"online": True},
                    PKG_B: {"online": True},
                },
            )
        detail = next(f["value"] for f in payload["embeds"][0]["fields"] if f["name"] == "Application Details")
        self.assertIn("4m 0s", detail)
        self.assertIn("1m 0s", detail)

    def test_relaunch_runtime_still_uses_online_session_start(self) -> None:
        supervisor = WatchdogSupervisor([ENTRY_A], {"supervisor": {}})
        supervisor._record_runtime_session_state(PKG_A, STATUS_LAUNCHING, STATUS_ONLINE, 100.0)
        supervisor._record_runtime_session_state(PKG_A, STATUS_ONLINE, STATUS_DEAD, 700.0)
        supervisor._record_runtime_session_state(PKG_A, STATUS_RELAUNCHING, STATUS_ONLINE, 800.0)
        self.assertTrue(supervisor._relaunch_runtime_active.get(PKG_A))
        started_at, source = supervisor._status_monitor_runtime_started_at(PKG_A, STATUS_ONLINE)
        self.assertEqual(started_at, 800.0)
        self.assertEqual(source, "relaunch_online_started_at")
        self.assertEqual(format_runtime_compact(803.0 - started_at), "3s")

    def test_persisted_launch_timestamp_survives_reload(self) -> None:
        ts = persist_package_launch_started(PKG_A, 4_000_000.0)
        self.assertEqual(ts, 4_000_000.0)
        reloaded = WatchdogSupervisor([ENTRY_A], {})
        self.assertEqual(reloaded._package_launch_started_at.get(PKG_A), 4_000_000.0)
        clear_package_launch_started(PKG_A)
        self.assertNotIn(PKG_A, load_package_launch_started_at())

    def test_fallback_monitor_started_at_when_launch_missing(self) -> None:
        monitor_at = 5_000_000.0
        supervisor = WatchdogSupervisor(
            [ENTRY_A],
            {"monitor_started_at": monitor_at},
            initial_status={PKG_A: STATUS_ONLINE},
        )
        supervisor._record_runtime_session_state(PKG_A, STATUS_PENDING, STATUS_ONLINE, monitor_at + 90.0)
        supervisor._online_start_ts.pop(PKG_A, None)
        started_at, source = supervisor._status_monitor_runtime_started_at(PKG_A, STATUS_ONLINE)
        self.assertEqual(source, "fallback_monitor_started_at")
        self.assertEqual(started_at, monitor_at)


if __name__ == "__main__":
    unittest.main()
