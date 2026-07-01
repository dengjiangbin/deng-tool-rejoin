"""
tests/test_stagger_fast_online_2026_07_01.py
============================================
Tests for the fixed stagger + fast Online detection architecture:

1. First launch uses Launching, never Relaunching.
2. Relaunching only after confirmed Dead/Disconnected/Join Failed.
3. gamejoinloadtime promotes Online immediately (PRIMARY_HOT_LANE_ONLY).
4. DENGRJN_HB (push_heartbeat) promotes Online immediately.
5. DENGRJN_JOIN promotes Online immediately (gamejoinloadtime source).
6. Stale HB (predates launch) cannot promote Online.
7. process_missing beats heartbeat — Dead, not No Heartbeat.
8. No Heartbeat + process_missing → Dead.
9. Online transition: initial_launch_inflight cleared.
10. initial_launch_inflight stays True until Online confirmation.
"""

from __future__ import annotations

import time
import threading
import unittest
from unittest.mock import patch, MagicMock

PKG = "com.roblox.clientab"
PKG2 = "com.roblox.clientbc"


def _make_monitor(*packages):
    """Create a monitor with all packages launched and process alive (mocked)."""
    from agent.rjn_lifecycle_monitor import RjnLifecycleMonitor
    pkgs = list(packages or (PKG,))
    m = RjnLifecycleMonitor(pkgs)
    # Mock _process_check to return (alive, [pid], False) so tests don't need ADB.
    m._process_check = lambda pkg: (True, ["12345"], False)
    for pkg in pkgs:
        m.note_launch_watchdog(pkg)
        with m._lock:
            row = m._states[pkg]
            row.process_exists = True
            row.process_seen_since_launch = True
            row.current_pid = "12345"
    return m


# =============================================================================
# Test 1: First launch shows Launching, never Relaunching
# =============================================================================

class T01_FirstLaunchIsLaunching(unittest.TestCase):
    """note_launch_watchdog(relaunch=False) must set initial_launch_inflight=True
    and internal_state=LAUNCHING."""

    def test_first_launch_sets_initial_launch_inflight(self):
        m = _make_monitor()
        with m._lock:
            row = m._states[PKG]
        self.assertTrue(row.initial_launch_inflight,
                        "First launch must set initial_launch_inflight=True")
        self.assertEqual(row.recovery_reason, "",
                         "First launch must have empty recovery_reason")
        self.assertEqual(row.relaunch_generation, 0,
                         "First launch must have relaunch_generation=0")

    def test_relaunch_clears_initial_launch_inflight(self):
        m = _make_monitor()
        m.note_launch_watchdog(PKG, relaunch=True)
        with m._lock:
            row = m._states[PKG]
        self.assertFalse(row.initial_launch_inflight,
                         "Recovery relaunch must clear initial_launch_inflight")
        self.assertNotEqual(row.recovery_reason, "",
                            "Recovery relaunch must set recovery_reason")
        self.assertEqual(row.relaunch_generation, 1,
                         "Recovery relaunch must increment relaunch_generation")

    def test_online_clears_initial_launch_inflight(self):
        m = _make_monitor()
        m.ingest_push_heartbeat(PKG, alive=True, place_id=1, universe_id=2, job_id="srv")
        with m._lock:
            row = m._states[PKG]
        self.assertFalse(row.initial_launch_inflight,
                         "Online confirmation must clear initial_launch_inflight")


# =============================================================================
# Test 2: gamejoinloadtime promotes Online immediately even in PRIMARY_HOT_LANE_ONLY
# =============================================================================

class T02_GamejoinloadtimePromotesOnline(unittest.TestCase):
    """gamejoinloadtime (native Roblox logcat) must set STATE_ONLINE_CONFIRMED
    even when PRIMARY_HOT_LANE_ONLY=True."""

    def test_gamejoinloadtime_sets_online_confirmed(self):
        from agent.rjn_lifecycle_monitor import STATE_ONLINE_CONFIRMED, PRIMARY_HOT_LANE_ONLY

        # This test only validates HOT_LANE behavior; ensure it's the default
        self.assertTrue(PRIMARY_HOT_LANE_ONLY,
                        "PRIMARY_HOT_LANE_ONLY must be True in default config")

        m = _make_monitor()
        # Directly call confirm_online_evidence with gamejoinloadtime source
        m.confirm_online_evidence(PKG, time.time(), source="gamejoinloadtime")

        with m._lock:
            row = m._states[PKG]
        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED,
                         "gamejoinloadtime must set STATE_ONLINE_CONFIRMED")
        ev = m.evaluate_package(PKG)
        self.assertTrue(ev.is_online_confirmed,
                        "evaluate_package must report online after gamejoinloadtime")

    def test_gamejoinloadtime_bypasses_20s_debounce(self):
        """gamejoinloadtime is definitive and must not be blocked by launch debounce."""
        from agent.rjn_lifecycle_monitor import STATE_ONLINE_CONFIRMED

        m = _make_monitor()
        with m._lock:
            row = m._states[PKG]
            # Set launch_started_at to 5s ago (inside the 20s debounce window)
            row.launch_started_at = time.time() - 5.0

        # gamejoinloadtime should still confirm Online (definitive, bypasses debounce)
        m.confirm_online_evidence(PKG, time.time(), source="gamejoinloadtime")
        with m._lock:
            row = m._states[PKG]
        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED,
                         "gamejoinloadtime must bypass 20s debounce")


# =============================================================================
# Test 3: DENGRJN_JOIN logcat line promotes Online immediately
# =============================================================================

class T03_DengrjnJoinPromotesOnline(unittest.TestCase):
    """_ingest_logcat_join_marker must confirm Online via gamejoinloadtime source."""

    def test_dengrjn_join_confirms_online(self):
        from agent.rjn_lifecycle_monitor import STATE_ONLINE_CONFIRMED

        m = _make_monitor()
        with m._lock:
            row = m._states[PKG]
            row.process_exists = True
            row.process_seen_since_launch = True
            sid = row.session_id

        # Simulate DENGRJN_JOIN line (v2 format)
        join_line = (
            f"DENGRJN_JOIN|{sid}|1|123456|987654321|100000|55555|job-abc-123"
        )
        result = m._ingest_logcat_join_marker(PKG, join_line, time.time())
        self.assertTrue(result, "DENGRJN_JOIN must be accepted")

        with m._lock:
            row = m._states[PKG]
        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED,
                         "DENGRJN_JOIN must set STATE_ONLINE_CONFIRMED")

    def test_dengrjn_join_different_sid_still_accepted(self):
        """DENGRJN_JOIN with a JobId-format session_id must still work.
        The session_id check is diagnostic only — not a hard gate."""
        from agent.rjn_lifecycle_monitor import STATE_ONLINE_CONFIRMED

        m = _make_monitor()
        with m._lock:
            row = m._states[PKG]
            row.process_exists = True
            row.process_seen_since_launch = True

        # Use a JobId-format session_id (like detector.lua sends)
        job_id_sid = "abc123-def456-ghi789-jkl012"
        join_line = (
            f"DENGRJN_JOIN|{job_id_sid}|1|123456|987654321|100000|55555|job-xyz"
        )
        result = m._ingest_logcat_join_marker(PKG, join_line, time.time())
        # Should be accepted (session_id is not rejected)
        self.assertTrue(result, "DENGRJN_JOIN with different session_id format must be accepted")

        with m._lock:
            row = m._states[PKG]
        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED,
                         "DENGRJN_JOIN with JobId session_id must set STATE_ONLINE_CONFIRMED")


# =============================================================================
# Test 4: push_heartbeat (DENGRJN_HB) promotes Online immediately
# =============================================================================

class T04_DengrjnHBPromotesOnline(unittest.TestCase):
    """DENGRJN_HB via logcat must confirm Online via push_heartbeat source."""

    def test_dengrjn_hb_confirms_online_v2(self):
        from agent.rjn_lifecycle_monitor import STATE_ONLINE_CONFIRMED

        m = _make_monitor()

        # Simulate DENGRJN_HB v2 line
        hb_line = "DENGRJN_HB|987654321|100000|55555|job-abc|1|g1_1234567|1|123456"
        result = m._ingest_logcat_heartbeat(PKG, hb_line, time.time())
        self.assertEqual(result, "online",
                         "DENGRJN_HB v2 must confirm Online")

        with m._lock:
            row = m._states[PKG]
        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED,
                         "DENGRJN_HB v2 must set STATE_ONLINE_CONFIRMED")

    def test_dengrjn_hb_confirms_online_v1(self):
        from agent.rjn_lifecycle_monitor import STATE_ONLINE_CONFIRMED

        m = _make_monitor()

        # Simulate DENGRJN_HB v1 line (no session_id)
        hb_line = "DENGRJN_HB|987654321|100000|55555|job-abc|1"
        result = m._ingest_logcat_heartbeat(PKG, hb_line, time.time())
        self.assertEqual(result, "online",
                         "DENGRJN_HB v1 must confirm Online")


# =============================================================================
# Test 5: Stale HB (predates launch) cannot promote Online
# =============================================================================

class T05_StaleHBCannotPromoteOnline(unittest.TestCase):
    """A heartbeat timestamped before launch_started_at must be rejected."""

    def test_predates_launch_hb_rejected(self):
        m = _make_monitor()
        with m._lock:
            row = m._states[PKG]
            launch_at = row.launch_started_at

        stale_at = launch_at - 5.0
        verdict = m.ingest_push_heartbeat(
            PKG, alive=True, place_id=111, universe_id=222, job_id="srv",
            at=stale_at,
        )
        self.assertNotEqual(verdict, "online",
                            "HB predating launch_started_at must be rejected")

    def test_predates_launch_gamejoin_rejected(self):
        """DENGRJN_JOIN marker with at < launch_started_at must be rejected."""
        m = _make_monitor()
        with m._lock:
            row = m._states[PKG]
            launch_at = row.launch_started_at
            sid = row.session_id

        stale_at = launch_at - 5.0
        join_line = f"DENGRJN_JOIN|{sid}|1|123456|987|100|55|job-xyz"
        result = m._ingest_logcat_join_marker(PKG, join_line, stale_at)
        self.assertFalse(result, "DENGRJN_JOIN predating launch must be rejected")


# =============================================================================
# Test 6: process_missing beats heartbeat → Dead, not No Heartbeat
# =============================================================================

class T06_ProcessMissingIsDead(unittest.TestCase):
    """When process is missing, force_close sets Dead regardless of HB state."""

    def test_process_gone_after_online_becomes_dead(self):
        from agent.rjn_lifecycle_monitor import STATE_DEAD

        m = _make_monitor()
        m.ingest_push_heartbeat(PKG, alive=True, place_id=1, universe_id=2, job_id="srv")

        # Simulate force-close: flip process check to report gone
        m._process_check = lambda pkg: (False, [], True)
        with m._lock:
            row = m._states[PKG]
            row.process_exists = False
            row.last_process_gone_at = time.time()
            row.current_pid = ""

        m.try_mark_force_close_dead(PKG)
        ev = m.evaluate_package(PKG)

        self.assertEqual(ev.internal_state, STATE_DEAD,
                         "process_missing must set STATE_DEAD")
        self.assertFalse(ev.is_online_confirmed)


# =============================================================================
# Test 7: No Heartbeat + process_missing → Dead
# =============================================================================

class T07_NHBProcessMissingIsDead(unittest.TestCase):
    """No Heartbeat with process missing must be Dead, not No Heartbeat."""

    def test_nhb_process_missing_becomes_dead(self):
        from agent.rjn_lifecycle_monitor import STATE_DEAD

        m = _make_monitor()
        # Never went online, process disappears
        m._process_check = lambda pkg: (False, [], True)
        with m._lock:
            row = m._states[PKG]
            row.process_exists = False
            row.last_process_gone_at = time.time()
            row.current_pid = ""
            row.ingame_hb_ever = False

        m.try_mark_force_close_dead(PKG)
        ev = m.evaluate_package(PKG)

        self.assertEqual(ev.internal_state, STATE_DEAD,
                         "NHB + process_missing must be Dead")


# =============================================================================
# Test 8: initial_launch_inflight cleared on Online (stagger can proceed)
# =============================================================================

class T08_OnlineClearsInflight(unittest.TestCase):
    """Online promotion must clear initial_launch_inflight so stagger moves on."""

    def test_online_clears_all_inflight(self):
        m = _make_monitor()

        with m._lock:
            row = m._states[PKG]
            self.assertTrue(row.initial_launch_inflight,
                            "Should be in initial_launch_inflight before Online")

        # Confirm Online
        m.ingest_push_heartbeat(PKG, alive=True, place_id=1, universe_id=2, job_id="srv")

        with m._lock:
            row = m._states[PKG]
        self.assertFalse(row.initial_launch_inflight,
                         "Online must clear initial_launch_inflight")
        self.assertFalse(row.relaunching,
                         "Online must clear relaunching flag")


# =============================================================================
# Test 9: PRIMARY_HOT_LANE_ONLY allows gamejoinloadtime but blocks scrape sources
# =============================================================================

class T09_HotLaneAllowsFastSources(unittest.TestCase):
    """In PRIMARY_HOT_LANE_ONLY mode, gamejoinloadtime and push_heartbeat pass
    but scrape/presence fallbacks are blocked."""

    def test_gamejoinloadtime_allowed_in_hot_lane(self):
        from agent.rjn_lifecycle_monitor import STATE_ONLINE_CONFIRMED, PRIMARY_HOT_LANE_ONLY

        self.assertTrue(PRIMARY_HOT_LANE_ONLY, "Must test with HOT_LANE enabled")

        m = _make_monitor()
        m.confirm_online_evidence(PKG, time.time(), source="gamejoinloadtime")
        with m._lock:
            row = m._states[PKG]
        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED)

    def test_push_heartbeat_allowed_in_hot_lane(self):
        from agent.rjn_lifecycle_monitor import STATE_ONLINE_CONFIRMED, PRIMARY_HOT_LANE_ONLY

        self.assertTrue(PRIMARY_HOT_LANE_ONLY)

        m = _make_monitor()
        m.confirm_online_evidence(PKG, time.time(), source="push_heartbeat")
        with m._lock:
            row = m._states[PKG]
        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED)

    def test_presence_blocked_in_hot_lane(self):
        """Presence/scrape fallbacks must be blocked in HOT_LANE mode."""
        from agent.rjn_lifecycle_monitor import STATE_ONLINE_CONFIRMED, PRIMARY_HOT_LANE_ONLY

        self.assertTrue(PRIMARY_HOT_LANE_ONLY)

        m = _make_monitor()
        m.confirm_online_evidence(PKG, time.time(), source="presence_in_experience")
        with m._lock:
            row = m._states[PKG]
        self.assertNotEqual(row.internal_state, STATE_ONLINE_CONFIRMED,
                            "presence_in_experience must be blocked in HOT_LANE mode")


# =============================================================================
# Test 10: New probe fields are populated
# =============================================================================

class T10_ProbeFieldsPopulated(unittest.TestCase):
    """evaluate_package detail must include all new required probe fields."""

    REQUIRED_FIELDS = [
        "initial_launch_inflight",
        "relaunch_inflight",
        "recovery_reason",
        "relaunch_generation",
        "am_start_at",
        "live_logcat_gamejoin_seen_at",
        "dump_logcat_gamejoin_seen_at",
        "detector_hb_seen_at",
        "online_promoted_at",
        "online_promoted_by",
        "next_package_allowed_at",
        "blocked_reason",
        "last_rejected_signal_reason",
    ]

    def test_all_probe_fields_present(self):
        m = _make_monitor()
        ev = m.evaluate_package(PKG)
        missing = [f for f in self.REQUIRED_FIELDS if f not in ev.detail]
        self.assertEqual(missing, [],
                         f"Missing probe fields: {missing}")

    def test_probe_fields_after_online(self):
        m = _make_monitor()
        m.ingest_push_heartbeat(PKG, alive=True, place_id=1, universe_id=2, job_id="srv")
        ev = m.evaluate_package(PKG)
        self.assertTrue(ev.is_online_confirmed)
        self.assertFalse(ev.detail.get("initial_launch_inflight"),
                         "initial_launch_inflight must be False after Online")
        self.assertNotEqual(ev.detail.get("online_promoted_at"), "",
                            "online_promoted_at must be set after Online")
        self.assertNotEqual(ev.detail.get("online_promoted_by"), "",
                            "online_promoted_by must be set after Online")

    def test_initial_launch_inflight_in_probe(self):
        m = _make_monitor()
        ev = m.evaluate_package(PKG)
        self.assertTrue(ev.detail.get("initial_launch_inflight"),
                        "initial_launch_inflight must be True before Online in probe")


# =============================================================================
# Test 11: Supervisor _initial_launch_inflight tracking
# =============================================================================

class T11_SupervisorInitialLaunchTracking(unittest.TestCase):
    """MultiPackageSupervisor must track initial_launch_inflight separately
    from _relaunch_inflight and clear both on STATUS_ONLINE."""

    def _make_supervisor(self, *pkgs):
        from agent.supervisor import WatchdogSupervisor, STATUS_READY
        from agent.rjn_lifecycle_monitor import RjnLifecycleMonitor
        import threading

        sup = WatchdogSupervisor.__new__(WatchdogSupervisor)
        sup.packages = list(pkgs or (PKG,))
        sup.entries = [{"package": p} for p in sup.packages]
        sup.entry_by_pkg = {p: {"package": p} for p in sup.packages}
        sup.cfg = {}
        sup.status_map = {p: STATUS_READY for p in sup.packages}
        sup._state_lock = threading.RLock()
        sup._package_opened = set()
        sup._relaunch_inflight = set()
        sup._initial_launch_inflight = set()
        sup._relaunch_verify_until = {}
        sup._missing_evidence_since = {}
        sup._all_launches_completed = False
        sup._prev_state = {}
        sup._last_launched_at = {}
        sup._push_last_seen = {}
        sup._push_ever_seen = set()
        sup._captcha_detected = {}
        sup._captcha_detected_at = {}
        sup._nhb_since = {}
        sup._nhb_offline_count = {}
        sup._nhb_cooldown_until = {}
        sup._last_any_launch_at = 0.0
        sup._package_launch_started_at = {}
        sup._grace_until = {}
        sup._revive_count = {}
        sup._failure_count = {}
        sup._recovery_launch_attempts = {}
        sup._recovery_throttle_until = {}
        sup._relaunch_runtime_active = {}
        sup._online_start_ts = {}
        sup._root_info = None
        sup.on_status_change = None
        rjn = RjnLifecycleMonitor(sup.packages)
        rjn._process_check = lambda p: (True, ["12345"], False)
        rjn.start_session = lambda: None
        sup._rjn_monitor = rjn
        sup._maybe_record_package_launch_started = lambda pkg, old: None
        return sup

    def test_mark_package_launched_sets_initial_inflight(self):
        from agent.supervisor import STATUS_LAUNCHING

        sup = self._make_supervisor(PKG)
        sup.mark_package_launched(PKG)

        self.assertIn(PKG, sup._initial_launch_inflight,
                      "mark_package_launched must add to _initial_launch_inflight")
        self.assertNotIn(PKG, sup._relaunch_inflight,
                         "mark_package_launched must NOT add to _relaunch_inflight")
        self.assertEqual(sup.status_map[PKG], STATUS_LAUNCHING)

    def test_set_status_online_clears_initial_inflight(self):
        from agent.supervisor import STATUS_ONLINE

        sup = self._make_supervisor(PKG)
        sup._initial_launch_inflight.add(PKG)
        sup._relaunch_inflight.add(PKG)
        # Stub callback
        sup._maybe_record_package_launch_started = lambda pkg, old: None
        sup._set_status(PKG, STATUS_ONLINE)

        self.assertNotIn(PKG, sup._initial_launch_inflight,
                         "STATUS_ONLINE must clear _initial_launch_inflight")
        self.assertNotIn(PKG, sup._relaunch_inflight,
                         "STATUS_ONLINE must clear _relaunch_inflight")


if __name__ == "__main__":
    unittest.main()
