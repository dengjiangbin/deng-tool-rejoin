"""Regression tests for probe p-9c18ae51bc follow-up:

1. Authoritative full `logcat -d` dump scan: a "Sending disconnect with reason: 278"
   (or other code) is detected and drives DISCONNECTED even when the live stream is
   silent/stalled — this is the fix for "278 still doesn't trigger recovery".
2. The dump scan respects ordering: a reconnect (newer gamejoinloadtime) after the
   kick re-confirms Online, and a stale pre-launch disconnect is ignored.
3. Deeplink / Wrong-Server when the configured link carries only a share/private
   code (no placeId): a session anchor on the first joined game flags a later move
   to a different game, and a share-code mismatch flags Wrong Server. Codes are
   compared as salted hashes only (never stored/uploaded raw).
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


def _online_monitor(pkg: str, uid: str) -> RjnLifecycleMonitor:
    mon = RjnLifecycleMonitor([pkg])
    mon._uid_map = {pkg: uid}
    mon._uid_to_package = {uid: pkg}
    mon._monitor_started_at = time.time() - 600
    row = mon._states[pkg]
    row.uid = uid
    row.pids = ["19470"]
    row.internal_state = STATE_ONLINE_CONFIRMED
    row.last_positive_online_evidence_at = time.time() - 30
    row.last_gamejoinloadtime_at = time.time() - 30
    row.online_since = time.time() - 30
    row.online_evidence_source = "gamejoinloadtime"
    return mon


def _dumpline(offset_sec: float, msg: str, pid: str = "19470", tid: str = "19540") -> str:
    ts = time.strftime("%m-%d %H:%M:%S", time.localtime(time.time() + offset_sec)) + ".000"
    return f"{ts}  {pid} {tid} I Roblox  : {msg}"


class DumpScanDisconnectTests(unittest.TestCase):
    def setUp(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def tearDown(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def test_dump_detects_278_when_stream_silent(self) -> None:
        pkg = "com.pkg.dump278"
        mon = _online_monitor(pkg, "10104")
        row = mon._states[pkg]
        line = _dumpline(0, "[FLog::Network] Sending disconnect with reason: 278")
        with patch.object(mon, "_dump_pkg_logcat", return_value=[line]):
            mon._scan_logcat_dump(pkg, time.time())
        self.assertEqual(row.internal_state, STATE_DISCONNECTED)
        self.assertEqual(row.last_transition_reason, "idle_disconnect_278")
        self.assertEqual(row.last_disconnect_code, 278)

    def test_dump_detects_generic_code(self) -> None:
        pkg = "com.pkg.dump285"
        mon = _online_monitor(pkg, "10105")
        row = mon._states[pkg]
        line = _dumpline(0, "[FLog::Network] Sending disconnect with reason: 285")
        with patch.object(mon, "_dump_pkg_logcat", return_value=[line]):
            mon._scan_logcat_dump(pkg, time.time())
        self.assertEqual(row.internal_state, STATE_DISCONNECTED)
        self.assertEqual(row.last_transition_reason, "disconnect_code_285")
        self.assertEqual(row.last_disconnect_code, 285)

    def test_dump_reconnect_after_kick_reconfirms_online(self) -> None:
        pkg = "com.pkg.reconnect"
        mon = _online_monitor(pkg, "10106")
        row = mon._states[pkg]
        row.internal_state = STATE_DISCONNECTED  # stuck disconnected, stream dead
        lines = [
            _dumpline(-10, "[FLog::Network] Sending disconnect with reason: 278"),
            _dumpline(-2, "[FLog::GameJoinLoadTime] Report game_join_loadtime: sid:abc, join_time:1.0"),
        ]
        with patch.object(mon, "_dump_pkg_logcat", return_value=lines):
            mon._scan_logcat_dump(pkg, time.time())
        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED)

    def test_dump_ignores_stale_pre_online_disconnect(self) -> None:
        pkg = "com.pkg.stale"
        mon = _online_monitor(pkg, "10107")
        row = mon._states[pkg]
        row.last_positive_online_evidence_at = time.time()  # online right now
        line = _dumpline(-300, "[FLog::Network] Sending disconnect with reason: 278")
        with patch.object(mon, "_dump_pkg_logcat", return_value=[line]):
            mon._scan_logcat_dump(pkg, time.time())
        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED)
        self.assertNotEqual(row.last_transition_reason, "idle_disconnect_278")

    def test_dump_disconnect_dedupes_same_line(self) -> None:
        pkg = "com.pkg.dedupe"
        mon = _online_monitor(pkg, "10108")
        row = mon._states[pkg]
        line = _dumpline(0, "[FLog::Network] Sending disconnect with reason: 278")
        with patch.object(mon, "_dump_pkg_logcat", return_value=[line]):
            mon._scan_logcat_dump(pkg, time.time())
            first_epoch = row.last_dump_disconnect_epoch
            row.last_dump_scan_at = 0.0  # bypass throttle
            mon._scan_logcat_dump(pkg, time.time())
        self.assertEqual(row.last_dump_disconnect_epoch, first_epoch)


class DeeplinkWrongServerTests(unittest.TestCase):
    def setUp(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def tearDown(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def test_session_anchor_flags_moved_link(self) -> None:
        pkg = "com.pkg.moved"
        mon = _online_monitor(pkg, "10109")
        # No expected target configured (share-code-only config has no placeId).
        mon._handle_logcat_line("uid=10109 I Roblox  : [FLog::GameJoinUtil] JoinGameNow placeId: 111111111")
        row = mon._states[pkg]
        self.assertTrue(row.anchor_set)
        self.assertEqual(row.anchor_place_id, 111111111)
        self.assertNotEqual(row.last_transition_reason, "wrong_server")
        # User moves to a different game/link → different placeId → Wrong Server.
        mon._handle_logcat_line("uid=10109 I Roblox  : [FLog::GameJoinUtil] JoinGameNow placeId: 222222222")
        self.assertEqual(row.observed_place_id, 222222222)
        self.assertEqual(row.last_transition_reason, "wrong_server")

    def test_share_code_mismatch_flags_wrong_server(self) -> None:
        pkg = "com.pkg.sharecode"
        mon = _online_monitor(pkg, "10110")
        mon.set_expected_target(pkg, private_code="EXPECTEDCODE123")
        row = mon._states[pkg]
        self.assertTrue(row.expected_private_code_hash)
        mon._handle_logcat_line(
            "uid=10110 I Roblox  : [FLog::ActivityProtocolLaunch] "
            "roblox://navigation/share_links?code=DIFFERENTCODE999&type=Server"
        )
        self.assertTrue(row.observed_private_code_hash)
        # Raw code must never be stored — only a short hash.
        self.assertNotIn("DIFFERENTCODE999", row.observed_private_code_hash)
        self.assertEqual(row.last_transition_reason, "wrong_server")

    def test_share_code_match_stays_online(self) -> None:
        pkg = "com.pkg.sharematch"
        mon = _online_monitor(pkg, "10111")
        mon.set_expected_target(pkg, private_code="SAMECODE777")
        row = mon._states[pkg]
        mon._handle_logcat_line(
            "uid=10111 I Roblox  : [FLog::ActivityProtocolLaunch] "
            "roblox://navigation/share_links?code=SAMECODE777&type=Server"
        )
        self.assertNotEqual(row.last_transition_reason, "wrong_server")

    def test_anchor_same_game_no_flag(self) -> None:
        pkg = "com.pkg.samegame"
        mon = _online_monitor(pkg, "10112")
        mon._handle_logcat_line("uid=10112 I Roblox  : [FLog::GameJoinUtil] JoinGameNow placeId: 333333333")
        mon._handle_logcat_line("uid=10112 I Roblox  : [FLog::GameJoinUtil] JoinGameNow placeId: 333333333")
        self.assertNotEqual(mon._states[pkg].last_transition_reason, "wrong_server")

    def test_relaunch_resets_anchor(self) -> None:
        pkg = "com.pkg.reanchor"
        mon = _online_monitor(pkg, "10113")
        mon._handle_logcat_line("uid=10113 I Roblox  : [FLog::GameJoinUtil] JoinGameNow placeId: 444444444")
        self.assertTrue(mon._states[pkg].anchor_set)
        mon.note_launch_watchdog(pkg, relaunch=True)
        row = mon._states[pkg]
        self.assertFalse(row.anchor_set)
        self.assertEqual(row.anchor_place_id, 0)
        self.assertEqual(row.observed_place_id, 0)


if __name__ == "__main__":
    unittest.main()
