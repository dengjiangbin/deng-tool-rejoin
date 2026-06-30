"""Regression tests for probe p-b967574a48.

User report: only the first clone flipped Online; the rest stayed Launching for
40+ minutes while logcat showed active DENGRJN_HB heartbeats on every clone.
Force-close was also not detected.

Root causes:
  * The live logcat stream recorded DENGRJN_HB lines in recent_uid_lines but
    _handle_logcat_line never ingested them — online proof waited for the slow
    5s poll / 3s PID dump path (minutes per round with 6 clones).
  * _process_check treated stale cached PIDs as instant-dead without pidof
    rediscovery, so clones 2–6 reported process_running=false forever.
  * _push_fresh only looked at the loopback detection worker, which fails on
    cloud phones (127.0.0.1:52789 connect fail in probe), so the fast watchdog
    path never activated even when logcat heartbeats were streaming.
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
    STATE_ONLINE_CONFIRMED,
)
from agent.supervisor import WatchdogSupervisor, PUSH_HEARTBEAT_FRESH_SECONDS


_HB_LINE = (
    "06-30 13:18:33.209 10105 10250 10390 I Roblox  : "
    "2026-06-30T06:18:32.934Z,2485.934082,b3bee230,6 [FLog::Output] "
    "DENGRJN_HB|121864768012064|121864768012064|6701277882|job-id|1"
)


def _monitor(*packages: str) -> RjnLifecycleMonitor:
    pkgs = list(packages) or ["com.moons.litesc", "com.moons.litesd"]
    mon = RjnLifecycleMonitor(pkgs)
    mon._monitor_started_at = time.time() - 120
    for pkg in pkgs:
        mon._uid_map[pkg] = {"com.moons.litesc": "10104", "com.moons.litesd": "10105"}.get(
            pkg, "10106"
        )
        mon._uid_to_package[mon._uid_map[pkg]] = pkg
        mon._states[pkg].uid = mon._uid_map[pkg]
    return mon


class LogcatStreamIngameHbTests(unittest.TestCase):
    def setUp(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def tearDown(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def test_live_stream_ingests_dengrjn_hb_instantly(self) -> None:
        pkg = "com.moons.litesd"
        mon = _monitor(pkg)
        mon.note_launch_watchdog(pkg, relaunch=False)
        mon._handle_logcat_line(_HB_LINE)
        row = mon._states[pkg]
        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED)
        self.assertTrue(row.ingame_hb_ever)
        self.assertGreater(row.last_positive_online_evidence_at, 0)

    def test_live_stream_hb_promotes_online_with_process(self) -> None:
        pkg = "com.moons.litesd"
        mon = _monitor(pkg)
        mon.note_launch_watchdog(pkg, relaunch=False)
        mon._handle_logcat_line(_HB_LINE)
        with patch.object(mon, "_process_check", return_value=(True, ["10250"])), \
             patch.object(mon, "_dump_pkg_logcat", return_value=[]):
            ev = mon.evaluate_package(pkg, fast_push=True)
        self.assertTrue(ev.is_online_confirmed)
        self.assertEqual(ev.internal_state, STATE_ONLINE_CONFIRMED)


class SupervisorLogcatFastPathTests(unittest.TestCase):
    def test_push_fresh_from_logcat_not_only_loopback(self) -> None:
        pkg = "com.moons.litesd"
        mon = _monitor(pkg)
        mon.note_launch_watchdog(pkg, relaunch=False)
        mon._handle_logcat_line(_HB_LINE)

        entry = {"package": pkg, "account_username": "user1"}
        sup = WatchdogSupervisor([entry], {"roblox_packages": [entry]})
        sup._rjn_monitor = mon
        sup._package_opened.add(pkg)

        with patch.object(sup, "_ingest_push_heartbeat", return_value=""):
            sup._sync_logcat_hb_push_channel(pkg)
        self.assertTrue(sup._push_fresh(pkg))

    def test_stale_pid_rediscovery_unblocks_online(self) -> None:
        pkg = "com.moons.litesd"
        mon = _monitor(pkg)
        mon.note_launch_watchdog(pkg, relaunch=False)
        mon._handle_logcat_line(_HB_LINE)
        mon._states[pkg].pids = ["999999999"]  # stale cache

        entry = {"package": pkg, "account_username": "user1"}
        sup = WatchdogSupervisor([entry], {"roblox_packages": [entry]})
        sup._rjn_monitor = mon
        sup._package_opened.add(pkg)
        sup.status_map[pkg] = "Launching"
        mon._root_info = type("RI", (), {"available": True, "tool": "su"})()

        fake_res = type("R", (), {"ok": True, "stdout": "10250"})()
        with patch.object(sup, "_ingest_push_heartbeat", return_value=""), \
             patch.object(rlm.android, "run_root_command", return_value=fake_res):
            state, detail = sup._detect_android_package_state(pkg)

        from agent.supervisor import STATUS_ONLINE

        self.assertEqual(state, STATUS_ONLINE)
        self.assertEqual(detail.get("process_running"), "true")
        self.assertLessEqual(float(PUSH_HEARTBEAT_FRESH_SECONDS), 15.0)


if __name__ == "__main__":
    unittest.main()
