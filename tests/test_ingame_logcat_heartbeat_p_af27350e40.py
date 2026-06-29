"""Regression tests for probe p-af27350e40.

User feedback: the logcat dump makes *online* detection instant and perfect, but
error code / captcha / dead states "stay online whole time" (no transition, so the
Account Dead webhook never posts). Those scenarios are GL/WebView-rendered events
that emit no parseable disconnect line on a background cloud-phone clone, and the
loopback HTTP push channel is sandboxed there.

Fix: the injected detector.lua now ALSO prints its heartbeat to logcat
("DENGRJN_HB|placeId|rootPlaceId|universeId|jobId|alive"), which the agent reads
with the same reliable PID-scoped dump it already uses for online detection. When
that heartbeat goes SILENT while the process is still alive, the client left the
live server, so the package is demoted to Disconnected (→ Account Dead webhook +
recovery) within ~10-13s.

These tests verify:
1. A logcat heartbeat confirms online and enrolls the package in loss detection.
2. Heartbeat silence past the grace (process alive) demotes ONLINE → DISCONNECTED
   with the "heartbeat_lost" reason — the universal error/captcha/dead path.
3. A still-flowing heartbeat keeps the package Online (no false demotion).
4. A heartbeat that reports a different game/server flags Wrong Server.
5. The supervisor treats a heartbeat_lost reason as a definitive dead detail so
   the Account Dead webhook arms.
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
    STATE_DISCONNECTED,
    STATE_ONLINE_CONFIRMED,
    INGAME_HB_LOSS_SECONDS,
)


def _hb_line(uid: str, place_id: int, universe_id: int, job_id: str = "abc-def", alive: int = 1) -> str:
    # Mirrors a real Roblox `print` surfacing in logcat under the package PID.
    return (
        f"06-29 22:40:44.282 {uid} 3721 3919 I Roblox  : "
        f"2026-06-29T15:40:44.282Z,500.8,b3eeb230,6 [FLog::Output] "
        f"DENGRJN_HB|{place_id}|{place_id}|{universe_id}|{job_id}|{alive}"
    )


def _monitor(pkg: str, uid: str) -> RjnLifecycleMonitor:
    mon = RjnLifecycleMonitor([pkg])
    mon._uid_map = {pkg: uid}
    mon._monitor_started_at = time.time() - 120
    row = mon._states[pkg]
    row.uid = uid
    row.launch_started_at = time.time() - 90
    return mon


class IngameLogcatHeartbeatTests(unittest.TestCase):
    def setUp(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def tearDown(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def test_heartbeat_confirms_online_and_enrolls_loss(self) -> None:
        pkg = "com.pkg.hbonline"
        mon = _monitor(pkg, "10104")
        now = time.time()
        verdict = mon.ingest_push_heartbeat(
            pkg, alive=True, place_id=121864768012064, universe_id=6701277882, at=now
        )
        self.assertEqual(verdict, "online")
        row = mon._states[pkg]
        self.assertTrue(row.ingame_hb_ever)
        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED)
        self.assertGreater(row.last_ingame_hb_at, 0.0)

    def test_heartbeat_silence_demotes_to_disconnected(self) -> None:
        pkg = "com.pkg.hbloss"
        mon = _monitor(pkg, "10104")
        # First: a real in-game heartbeat → online + enrolled.
        mon.ingest_push_heartbeat(
            pkg, alive=True, place_id=121864768012064, universe_id=6701277882,
            at=time.time(),
        )
        row = mon._states[pkg]
        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED)
        # Now the heartbeat stops: backdate the last beat past the loss grace.
        row.last_ingame_hb_at = time.time() - (INGAME_HB_LOSS_SECONDS + 5)
        # Process still alive, but no fresh beat in the dump.
        with patch.object(mon, "_process_check", return_value=(True, ["3721"])), \
             patch.object(mon, "_dump_pkg_logcat", return_value=[]):
            ev = mon.evaluate_package(pkg)
        self.assertEqual(ev.internal_state, STATE_DISCONNECTED)
        self.assertFalse(ev.is_online_confirmed)
        self.assertEqual(mon._states[pkg].last_transition_reason, "heartbeat_lost")
        friendly = ev.detail.get("reason_user_friendly", "").lower()
        self.assertTrue(friendly)

    def test_fresh_heartbeat_keeps_online_no_false_demotion(self) -> None:
        pkg = "com.pkg.hbfresh"
        mon = _monitor(pkg, "10104")
        mon.ingest_push_heartbeat(
            pkg, alive=True, place_id=121864768012064, universe_id=6701277882,
            at=time.time(),
        )
        # A NEW heartbeat line is present in the dump (current device epoch).
        line = _hb_line("10104", 121864768012064, 6701277882)
        with patch.object(mon, "_process_check", return_value=(True, ["3721"])), \
             patch.object(mon, "_dump_pkg_logcat", return_value=[line]), \
             patch.object(mon, "_logcat_line_epoch", side_effect=lambda *_a, **_k: time.time()):
            ev = mon.evaluate_package(pkg)
        self.assertTrue(ev.is_online_confirmed)
        self.assertEqual(ev.internal_state, STATE_ONLINE_CONFIRMED)

    def test_dump_heartbeat_line_confirms_online(self) -> None:
        pkg = "com.pkg.hbdump"
        mon = _monitor(pkg, "10104")
        line = _hb_line("10104", 121864768012064, 6701277882)
        with patch.object(mon, "_process_check", return_value=(True, ["3721"])), \
             patch.object(mon, "_dump_pkg_logcat", return_value=[line]), \
             patch.object(mon, "_logcat_line_epoch", side_effect=lambda *_a, **_k: time.time()):
            ev = mon.evaluate_package(pkg)
        self.assertTrue(ev.is_online_confirmed)
        self.assertTrue(mon._states[pkg].ingame_hb_ever)

    def test_wrong_game_heartbeat_flags_wrong_server(self) -> None:
        pkg = "com.pkg.hbwrong"
        mon = _monitor(pkg, "10104")
        mon.set_expected_target(pkg, place_id=111111111, universe_id=222222222)
        verdict = mon.ingest_push_heartbeat(
            pkg, alive=True, place_id=999999999, universe_id=888888888, at=time.time()
        )
        self.assertEqual(verdict, "wrong_server")
        with patch.object(mon, "_process_check", return_value=(True, ["3721"])), \
             patch.object(mon, "_dump_pkg_logcat", return_value=[]):
            ev = mon.evaluate_package(pkg)
        self.assertEqual(ev.internal_state, STATE_DISCONNECTED)
        self.assertEqual(mon._states[pkg].last_transition_reason, "wrong_server")

    def test_launch_resets_heartbeat_enrollment(self) -> None:
        pkg = "com.pkg.hbreset"
        mon = _monitor(pkg, "10104")
        mon.ingest_push_heartbeat(pkg, alive=True, place_id=1, universe_id=2, at=time.time())
        self.assertTrue(mon._states[pkg].ingame_hb_ever)
        mon.note_launch_watchdog(pkg, relaunch=True)
        self.assertFalse(mon._states[pkg].ingame_hb_ever)
        self.assertEqual(mon._states[pkg].last_ingame_hb_at, 0.0)


class HeartbeatLineMatchTests(unittest.TestCase):
    def test_logcat_detector_emits_ingame_hb_event(self) -> None:
        from agent.android_logcat_detector import _match_line_events

        line = _hb_line("10104", 121864768012064, 6701277882)
        events = _match_line_events("com.pkg.x", line, time.time())
        self.assertTrue(any(e.event == "package_logcat_ingame_hb" for e in events))

    def test_regex_parses_payload(self) -> None:
        m = rlm._INGAME_HB_RE.search("[FLog::Output] DENGRJN_HB|123|123|456|job-guid|1")
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.group(1), "123")
        self.assertEqual(m.group(3), "456")
        self.assertEqual(m.group(5), "1")


class DefinitiveDeadReasonTests(unittest.TestCase):
    def test_heartbeat_lost_is_definitive_dead(self) -> None:
        from agent.supervisor import WatchdogSupervisor

        detail = {"reason_internal": "heartbeat_lost"}
        # Use the unbound method with a minimal object exposing the helper only.
        self.assertTrue(
            WatchdogSupervisor._definitive_dead_detail(
                WatchdogSupervisor.__new__(WatchdogSupervisor), detail
            )
        )


if __name__ == "__main__":
    unittest.main()
