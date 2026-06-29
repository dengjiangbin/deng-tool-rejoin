"""Regression tests for probe p-daee3387a8 follow-up:

1. The authoritative Roblox disconnect line ("Sending disconnect with reason: N")
   is parsed into a numeric Error Code so 278 (idle) and other codes are detected
   with a correct user-facing reason — and drive recovery via the live path.
2. Wrong-Server detection: when the client provably joins a placeId different from
   the configured target, the package flips to DISCONNECTED (reason wrong_server)
   so the supervisor relaunches it. Strictly fail-safe: never fires without BOTH a
   known expected and a known observed id.
3. Faster recovery: the slow dumpsys/uiautomator disconnect scan is skipped while
   the logcat stream is fresh (the stream already catches "with reason" instantly).
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
from agent.rjn_lifecycle_monitor import (
    RjnLifecycleMonitor,
    STATE_DISCONNECTED,
    STATE_ONLINE_CONFIRMED,
)
from agent import roblox_health
from agent.roblox_disconnect_reasons import format_lifecycle_dead_reason


def _online_monitor(pkg: str, uid: str) -> RjnLifecycleMonitor:
    mon = RjnLifecycleMonitor([pkg])
    mon._uid_map = {pkg: uid}
    mon._uid_to_package = {uid: pkg}
    mon._monitor_started_at = time.time() - 60
    row = mon._states[pkg]
    row.uid = uid
    row.internal_state = STATE_ONLINE_CONFIRMED
    row.last_positive_online_evidence_at = time.time() - 30
    row.last_gamejoinloadtime_at = time.time() - 30
    row.online_since = time.time() - 30
    row.online_evidence_source = "gamejoinloadtime"
    return mon


class DisconnectReasonCodeTests(unittest.TestCase):
    def setUp(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def tearDown(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def test_idle_278_with_reason_line_parses_code_and_reason(self) -> None:
        pkg = "com.pkg.code278"
        mon = _online_monitor(pkg, "10104")
        line = (
            "uid=10104 I Roblox  : 2026-06-28T12:15:04.721Z,1216.7,b8b09230,7 "
            "[FLog::Network] Sending disconnect with reason: 278"
        )
        mon._handle_logcat_line(line)
        with patch.object(mon, "_process_check", return_value=(True, ["1"])):
            ev = mon.evaluate_package(pkg)
        row = mon._states[pkg]
        self.assertEqual(ev.internal_state, STATE_DISCONNECTED)
        self.assertFalse(ev.is_online_confirmed)
        self.assertEqual(row.last_disconnect_code, 278)
        self.assertEqual(row.last_transition_reason, "idle_disconnect_278")
        friendly = ev.detail.get("reason_user_friendly", "").lower()
        self.assertIn("278", friendly)
        self.assertIn("idle", friendly)

    def test_other_code_268_with_reason_line_surfaces_code(self) -> None:
        pkg = "com.pkg.code268"
        mon = _online_monitor(pkg, "10105")
        line = (
            "uid=10105 I Roblox  : [FLog::Network] Sending disconnect with reason: 268"
        )
        mon._handle_logcat_line(line)
        with patch.object(mon, "_process_check", return_value=(True, ["2"])):
            ev = mon.evaluate_package(pkg)
        self.assertEqual(ev.internal_state, STATE_DISCONNECTED)
        self.assertEqual(mon._states[pkg].last_disconnect_code, 268)
        self.assertIn("268", ev.detail.get("reason_user_friendly", ""))

    def test_handshake_reason_zero_not_treated_as_error_code(self) -> None:
        # "with reason: 0" handshake noise must not masquerade as an error code.
        self.assertIsNone(
            __import__("agent.roblox_disconnect_reasons", fromlist=["x"]).parse_roblox_error_code(
                "Sending disconnect with reason: 0"
            )
        )


class WrongServerTests(unittest.TestCase):
    def setUp(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def tearDown(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def test_place_mismatch_flags_wrong_server(self) -> None:
        pkg = "com.pkg.wrong"
        mon = _online_monitor(pkg, "10106")
        mon.set_expected_target(pkg, place_id=111111111)
        join_line = (
            "uid=10106 I Roblox  : [FLog::GameJoinUtil] GameJoinUtil_JoinGameNow "
            "placeId: 222222222"
        )
        mon._handle_logcat_line(join_line)
        with patch.object(mon, "_process_check", return_value=(True, ["3"])):
            ev = mon.evaluate_package(pkg)
        row = mon._states[pkg]
        self.assertEqual(ev.internal_state, STATE_DISCONNECTED)
        self.assertFalse(ev.is_online_confirmed)
        self.assertEqual(row.last_transition_reason, "wrong_server")
        self.assertEqual(row.observed_place_id, 222222222)
        # probe p-630c95f7cc #3: wrong-server now reads "Account is not in
        # configured server" (detection unchanged, reason text only).
        self.assertEqual(
            ev.detail.get("reason_user_friendly", "").lower(),
            "account is not in configured server",
        )

    def test_place_match_stays_online(self) -> None:
        pkg = "com.pkg.right"
        mon = _online_monitor(pkg, "10107")
        mon.set_expected_target(pkg, place_id=111111111)
        join_line = (
            "uid=10107 I Roblox  : [FLog::GameJoinUtil] JoinGameNow placeId: 111111111"
        )
        mon._handle_logcat_line(join_line)
        with patch.object(mon, "_process_check", return_value=(True, ["4"])):
            ev = mon.evaluate_package(pkg)
        self.assertNotEqual(mon._states[pkg].last_transition_reason, "wrong_server")
        self.assertEqual(mon._states[pkg].observed_place_id, 111111111)

    def test_unknown_expected_never_flags(self) -> None:
        pkg = "com.pkg.noexpect"
        mon = _online_monitor(pkg, "10108")
        # No set_expected_target → expected unknown → must never flag wrong server.
        join_line = (
            "uid=10108 I Roblox  : [FLog::GameJoinUtil] JoinGameNow placeId: 222222222"
        )
        mon._handle_logcat_line(join_line)
        self.assertNotEqual(mon._states[pkg].last_transition_reason, "wrong_server")

    def test_root_place_id_does_not_falsely_match_place_pattern(self) -> None:
        pkg = "com.pkg.root"
        mon = _online_monitor(pkg, "10109")
        mon.set_expected_target(pkg, place_id=111111111, root_place_id=999999999)
        # rootPlaceId matches expected root; no generic placeId present → no mismatch.
        join_line = "uid=10109 I Roblox  : [FLog::GameJoin] rootPlaceId=999999999"
        mon._handle_logcat_line(join_line)
        row = mon._states[pkg]
        self.assertEqual(row.observed_root_place_id, 999999999)
        self.assertEqual(row.observed_place_id, 0)
        self.assertNotEqual(row.last_transition_reason, "wrong_server")


class StreamFreshnessGateTests(unittest.TestCase):
    def test_stream_fresh_requires_alive_and_watched_phrase(self) -> None:
        pkg = "com.pkg.fresh"
        mon = RjnLifecycleMonitor([pkg])
        mon._uid_map = {pkg: "10110"}
        mon._uid_to_package = {"10110": pkg}
        mon._monitor_started_at = time.time() - 60
        mon._logcat_stream_alive = True
        # Generic perfdata tick updates last_uid_line_at only — must NOT count as fresh.
        mon._handle_logcat_line(
            "uid=10110 I rbx.perfdata: perfdata battery AC CHARGING 0uAmps 0mW"
        )
        self.assertGreater(mon._states[pkg].last_uid_line_at, 0)
        self.assertFalse(mon.stream_fresh_for(pkg, 40.0))
        # Watched join phrase → fresh for disconnect-skip purposes.
        mon._handle_logcat_line("uid=10110 I Roblox  : gamejoinloadtime sid:abc")
        self.assertTrue(mon.stream_fresh_for(pkg, 40.0))
        # Age out the last watched event → stale again.
        mon._states[pkg].last_logcat_event_at = time.time() - 120
        mon._states[pkg].last_gamejoinloadtime_at = time.time() - 120
        self.assertFalse(mon.stream_fresh_for(pkg, 40.0))

    def test_perfdata_only_does_not_block_disconnect_scan(self) -> None:
        pkg = "com.pkg.perfdata"
        mon = _online_monitor(pkg, "10111")
        row = mon._states[pkg]
        row.last_logcat_event_at = time.time() - 120
        row.last_gamejoinloadtime_at = time.time() - 120
        row.last_positive_online_evidence_at = time.time() - 120
        mon._logcat_stream_alive = True
        mon._handle_logcat_line(
            "uid=10111 I rbx.perfdata: perfdata battery AC CHARGING 0uAmps 0mW"
        )
        self.assertFalse(mon.stream_fresh_for(pkg, 40.0))
        with patch.object(mon, "_process_check", return_value=(True, ["999"])), \
             patch.object(mon, "_detect_live_disconnect", return_value=("idle_disconnect_278", "Error Code: 278")) as disc:
            mon.evaluate_package(pkg)
        disc.assert_called_once()
        self.assertEqual(row.internal_state, STATE_DISCONNECTED)


class LegacyDisconnectPathTests(unittest.TestCase):
    """roblox_health.analyze_disconnect_signals must recognise the real network
    line so the fallback recovery path also catches 278 (defense in depth)."""

    def test_with_reason_278_classified_idle(self) -> None:
        blob = (
            "06-28 19:15:04.486 10104 8631 8708 I Roblox  : "
            "[FLog::Network] Sending disconnect with reason: 278"
        )
        with patch.object(roblox_health, "_pid_for_package", return_value="8631"), \
             patch.object(roblox_health, "_brief_logcat_for_pid", return_value=blob):
            ev = roblox_health.analyze_disconnect_signals("com.roblox.client")
        self.assertIsNotNone(ev)
        self.assertEqual(ev.category, "idle_disconnect")

    def test_with_reason_generic_code_classified_disconnected(self) -> None:
        blob = "I Roblox  : [FLog::Network] Sending disconnect with reason: 268"
        with patch.object(roblox_health, "_pid_for_package", return_value="8631"), \
             patch.object(roblox_health, "_brief_logcat_for_pid", return_value=blob):
            ev = roblox_health.analyze_disconnect_signals("com.roblox.client")
        self.assertIsNotNone(ev)
        self.assertEqual(ev.category, "disconnected")

    def test_wrong_server_reason_text(self) -> None:
        self.assertEqual(
            format_lifecycle_dead_reason("wrong_server"),
            "Account is not in configured server",
        )


if __name__ == "__main__":
    unittest.main()
