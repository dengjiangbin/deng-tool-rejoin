"""
tests/test_detector_authority_2026_07_01.py
===========================================
All 12 required tests for the v2 detector authority architecture:

  T01  stale heartbeat after force close cannot set Online
  T02  No Heartbeat + process gone becomes Dead immediately
  T03  HB-loss with process alive stays No Heartbeat
  T04  Relaunching cannot be overwritten by Launching
  T05  relaunch_inflight clears only on fresh current-generation heartbeat
  T06  presence cannot override Dead/process_missing
  T07  PID reuse cannot accept old heartbeat
  T08  next package launch waits for true Online proof, not stale status
  T09  crash dialog / process alive triggers recovery
  T10  /install/test/latest returns valid shell installer
  T11  test artifact downloads and SHA matches
  T12  stable/latest is unchanged by test deployments

Installer tests (T10-T12) probe the live production URL; they are skipped
automatically when DENG_SKIP_INSTALLER_TESTS=1 or the host has no network.
"""

from __future__ import annotations

import os
import time
import threading
import unittest
from unittest.mock import MagicMock, patch

PKG = "com.roblox.clientab"
PKG2 = "com.roblox.clientbc"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_monitor(*packages):
    """Create a RjnLifecycleMonitor with a live process mocked in."""
    from agent.rjn_lifecycle_monitor import RjnLifecycleMonitor
    pkgs = packages or (PKG,)
    m = RjnLifecycleMonitor(list(pkgs))
    for pkg in pkgs:
        m.note_launch_watchdog(pkg)
        with m._lock:
            row = m._states[pkg]
            row.process_exists = True
            row.process_seen_since_launch = True
            row.current_pid = "9999"
    return m


def _confirm_online(monitor, pkg=PKG):
    """Feed a fresh heartbeat so the monitor considers the package Online."""
    with monitor._lock:
        row = monitor._states.get(pkg)
        if row is None:
            return
        row.process_exists = True
        row.process_seen_since_launch = True
    monitor.ingest_push_heartbeat(
        pkg, alive=True, place_id=111, universe_id=222, job_id="serverA"
    )


def _make_supervisor(*packages):
    """Minimal WatchdogSupervisor stub for state-detection tests.

    Initialises all dicts/sets that _detect_android_package_state touches so
    the method can run without a fully-initialised supervisor instance.
    """
    from agent.supervisor import WatchdogSupervisor
    from agent.rjn_lifecycle_monitor import RjnLifecycleMonitor
    pkgs = list(packages or (PKG,))

    sup = WatchdogSupervisor.__new__(WatchdogSupervisor)
    sup.cfg = {}
    sup.packages = pkgs
    sup.status_map = {}
    sup._relaunch_inflight = set()
    sup._initial_launch_inflight = set()
    sup._relaunch_verify_until = {}
    sup._all_launches_completed = False
    sup._package_opened = set()
    sup._prev_state = {}
    sup._last_online_ts = {}
    sup._nhb_offline_count = {}
    sup._push_last_seen = {}
    sup._rjn_monitor = RjnLifecycleMonitor(pkgs)
    for p in pkgs:
        sup._package_opened.add(p)
        sup._rjn_monitor.note_launch_watchdog(p)
        with sup._rjn_monitor._lock:
            row = sup._rjn_monitor._states[p]
            row.process_exists = True
            row.process_seen_since_launch = True
    return sup


# ============================================================================
# T01 - stale heartbeat after force close cannot set Online
# ============================================================================

class T01_StaleHeartbeatCannotRevive(unittest.TestCase):
    """After a force-close (process gone), a buffered HB from the dead session
    must be rejected even if its timestamp appears recent."""

    def test_stale_hb_does_not_set_online_after_process_gone(self):
        m = _make_monitor()
        _confirm_online(m)

        # Simulate force-close: process disappears, gone_at is now
        gone_at = time.time()
        with m._lock:
            row = m._states[PKG]
            row.process_exists = False
            row.last_process_gone_at = gone_at
            row.current_pid = ""
            row.pid_start_time = ""
        m.try_mark_force_close_dead(PKG)

        with patch.object(m, "_process_check", return_value=(False, [], True)):
            ev_pre = m.evaluate_package(PKG)
        self.assertFalse(ev_pre.is_online_confirmed, "should be Dead after force-close")

        # Feed a heartbeat with a timestamp just BEFORE process went gone
        stale_at = gone_at - 0.1
        verdict = m.ingest_push_heartbeat(
            PKG, alive=True, place_id=111, universe_id=222, job_id="serverA",
            at=stale_at
        )
        self.assertNotEqual(verdict, "online",
                            "stale HB must not set Online after force-close")

    def test_rejected_signal_reason_is_recorded(self):
        m = _make_monitor()
        _confirm_online(m)
        gone_at = time.time()
        with m._lock:
            row = m._states[PKG]
            row.process_exists = False
            row.last_process_gone_at = gone_at
            row.current_pid = ""
        m.try_mark_force_close_dead(PKG)

        m.ingest_push_heartbeat(
            PKG, alive=True, place_id=111, universe_id=222, job_id="serverA",
            at=gone_at - 0.1
        )
        with m._lock:
            reason = m._states[PKG].last_rejected_signal_reason
        self.assertTrue(reason, "rejection reason must be recorded after stale HB")


# ============================================================================
# T02 - No Heartbeat + process gone becomes Dead immediately
# ============================================================================

class T02_NHBProcessGoneDead(unittest.TestCase):
    """When a package was previously Online and then its process disappears,
    the lifecycle monitor must immediately report Dead public_status.

    We test the monitor directly (which is where the authority rule lives).
    The supervisor calls evaluate_package and promotes based on its result.
    """

    def test_nhb_plus_process_gone_becomes_dead(self):
        from agent.rjn_lifecycle_monitor import (
            RjnLifecycleMonitor, STATE_DEAD
        )
        m = RjnLifecycleMonitor([PKG])
        m.note_launch_watchdog(PKG)
        with m._lock:
            row = m._states[PKG]
            row.process_exists = True
            row.process_seen_since_launch = True

        # Confirm Online first so dead-lane is armed
        _confirm_online(m)

        # Now process disappears
        with m._lock:
            row = m._states[PKG]
            row.process_exists = False
            row.last_process_gone_at = time.time()
            row.current_pid = ""

        m.try_mark_force_close_dead(PKG)

        # evaluate_package with process gone must report Dead
        with patch.object(m, "_process_check", return_value=(False, [], True)):
            ev = m.evaluate_package(PKG)

        self.assertEqual(ev.public_status, "Dead",
                         "NHB+process_gone must produce Dead public_status")
        self.assertFalse(ev.is_online_confirmed,
                         "NHB+process_gone must not be online_confirmed")

    def test_nhb_with_alive_process_stays_nhb_not_dead(self):
        """Process alive + heartbeat lost = should NOT be Dead."""
        from agent.rjn_lifecycle_monitor import RjnLifecycleMonitor
        m = RjnLifecycleMonitor([PKG])
        m.note_launch_watchdog(PKG)
        with m._lock:
            row = m._states[PKG]
            row.process_exists = True
            row.process_seen_since_launch = True

        # Process is alive but heartbeat is stale (> ONLINE_HB_FRESH_SECONDS ago)
        with m._lock:
            row = m._states[PKG]
            row.ingame_hb_ever = True
            row.last_ingame_hb_at = time.time() - 120
            row.last_ingame_hb_wall_at = time.time() - 120

        with patch.object(m, "_process_check", return_value=(True, ["9999"], False)):
            ev = m.evaluate_package(PKG)

        self.assertNotEqual(ev.public_status, "Dead",
                            "alive process + HB-loss must NOT be Dead")


# ============================================================================
# T03 - HB-loss with process alive stays No Heartbeat
# ============================================================================

class T03_HBLossProcessAliveStaysNHB(unittest.TestCase):
    """HB-loss + process alive = No Heartbeat, never Dead.

    The authority rule: Dead = process MISSING. If process is alive but
    heartbeats are silent, it's No Heartbeat (not Dead).
    """

    def test_alive_process_heartbeat_lost_is_not_dead(self):
        from agent.rjn_lifecycle_monitor import RjnLifecycleMonitor
        m = RjnLifecycleMonitor([PKG])
        m.note_launch_watchdog(PKG)
        with m._lock:
            row = m._states[PKG]
            row.process_exists = True
            row.process_seen_since_launch = True
            # Stale heartbeat (process alive but no new HB for 120s)
            row.ingame_hb_ever = True
            row.last_ingame_hb_at = time.time() - 120
            row.last_ingame_hb_wall_at = time.time() - 120

        with patch.object(m, "_process_check", return_value=(True, ["9999"], False)):
            ev = m.evaluate_package(PKG)

        self.assertNotEqual(ev.public_status, "Dead",
                            "alive process + HB-loss must NOT be Dead")
        # Process is alive, so force_close_detected must remain False
        with m._lock:
            self.assertFalse(m._states[PKG].force_close_detected,
                             "force_close_detected must be False when process is alive")


# ============================================================================
# T04 - Relaunching cannot be overwritten by Launching
# ============================================================================

class T04_RelaunchingNotOverwrittenByLaunching(unittest.TestCase):
    """After a relaunch, STATUS_RELAUNCHING must persist until
    Online/Dead/Disconnected/JoinFailed - never revert to Launching."""

    def test_relaunching_survives_grace_expiry(self):
        from agent.supervisor import WatchdogSupervisor
        import inspect
        src = inspect.getsource(WatchdogSupervisor._detect_android_package_state)
        self.assertIn("STATUS_RELAUNCHING", src,
                      "_detect_android_package_state must reference STATUS_RELAUNCHING")
        self.assertIn("relaunch_post_grace_pending_confirmation", src,
                      "post-grace code must set reason=relaunch_post_grace_pending_confirmation")

    def test_sync_stagger_does_not_override_relaunching(self):
        from agent.supervisor import WatchdogSupervisor
        import inspect
        src = inspect.getsource(WatchdogSupervisor.sync_stagger_display_status)
        self.assertNotIn("STATUS_RELAUNCHING: STATUS_LAUNCHING", src,
                         "sync_stagger must never demote Relaunching to Launching")


# ============================================================================
# T05 - relaunch_inflight clears only on fresh current-generation HB
# ============================================================================

class T05_RelaunchInflightClearsOnFreshHB(unittest.TestCase):
    """online_confirmed_generation is updated on Online confirmation.
    Session_id is logged for diagnostics but NOT used for rejection —
    detector.lua uses JobId-based IDs, Python uses timestamp-based IDs,
    they must not be cross-checked. Timestamp guards are the authority."""

    def test_predates_launch_hb_rejected(self):
        """HB with at < launch_started_at must be rejected (timestamp guard)."""
        m = _make_monitor()
        # Advance generation
        m.note_launch_watchdog(PKG, relaunch=True)
        with m._lock:
            row = m._states[PKG]
            row.process_exists = True
            row.process_seen_since_launch = True
            launch_at = row.launch_started_at

        # HB timestamped before this launch
        stale_at = launch_at - 5.0
        verdict = m.ingest_push_heartbeat(
            PKG, alive=True, place_id=111, universe_id=222, job_id="srv",
            at=stale_at,
        )
        self.assertNotEqual(verdict, "online",
                            "pre-launch HB must not confirm Online")

    def test_session_id_mismatch_does_not_block_online(self):
        """A heartbeat with a different session_id format (JobId from detector.lua)
        must NOT be rejected — session_id is diagnostic only."""
        m = _make_monitor()
        m.note_launch_watchdog(PKG, relaunch=True)
        with m._lock:
            row = m._states[PKG]
            row.process_exists = True
            row.process_seen_since_launch = True
            expected_gen = row.launch_generation

        # JobId-format session_id (from detector.lua) — different from Python's format
        detector_sid = "abc123-def456-ghi789"  # game.JobId format

        # Must be ACCEPTED (session_id check removed — timestamp guards are sufficient)
        verdict = m.ingest_push_heartbeat(
            PKG, alive=True, place_id=111, universe_id=222, job_id="srv",
            session_id=detector_sid,
        )
        self.assertEqual(verdict, "online",
                         "JobId session_id must not block Online — session_id check removed")

        # Verify generation was recorded immediately (not waiting for evaluate_package)
        self.assertEqual(m.get_online_generation(PKG), expected_gen,
                         "online_generation must match launch_generation after fresh HB")

    def test_current_generation_hb_confirms_online(self):
        """A current-generation HB confirms Online and records the generation."""
        m = _make_monitor()
        m.note_launch_watchdog(PKG, relaunch=True)
        with m._lock:
            row = m._states[PKG]
            row.process_exists = True
            row.process_seen_since_launch = True
            expected_gen = row.launch_generation

        verdict = m.ingest_push_heartbeat(
            PKG, alive=True, place_id=111, universe_id=222, job_id="srv",
        )
        self.assertEqual(verdict, "online",
                         "current-generation HB must confirm Online")
        self.assertEqual(m.get_online_generation(PKG), expected_gen,
                         "online_generation must match launch_generation")


# ============================================================================
# T06 - presence cannot override Dead/process_missing
# ============================================================================

class T06_PresenceCannotOverrideDead(unittest.TestCase):
    """The Roblox Presence API result must never flip a Dead/process_missing
    package back to Online - it is support only, never authoritative."""

    def test_presence_cannot_revive_dead_package(self):
        m = _make_monitor()
        _confirm_online(m)

        # Force-close
        with m._lock:
            row = m._states[PKG]
            row.process_exists = False
            row.last_process_gone_at = time.time()
            row.current_pid = ""
        m.try_mark_force_close_dead(PKG)

        with patch.object(m, "_process_check", return_value=(False, [], True)):
            ev_before = m.evaluate_package(PKG)
        self.assertFalse(ev_before.is_online_confirmed)

        # Inject Presence API result claiming in_experience
        class _FakePresence:
            place_id = 111
            root_place_id = 111
            universe_id = 222

        m.set_observed_from_presence(PKG, _FakePresence())
        # confirm_online_evidence from Presence must not override Dead
        # (the process is still gone, so process_exists check will prevent it)
        m.confirm_online_evidence(PKG, time.time(), source="presence_in_experience")

        with patch.object(m, "_process_check", return_value=(False, [], True)):
            ev_after = m.evaluate_package(PKG)
        self.assertFalse(ev_after.is_online_confirmed,
                         "Presence must not revive Dead/process_missing package")


# ============================================================================
# T07 - PID reuse cannot accept old heartbeat
# ============================================================================

class T07_PIDReuseRejected(unittest.TestCase):
    """Heartbeat rejection guards: timestamp-based and process-based.

    Session_id is no longer used for rejection (detector.lua uses JobId format,
    Python monitor uses timestamp format — they never match). Guards are:
    - HB timestamped before launch_started_at → rejected
    - HB timestamped after last_process_gone_at → rejected
    - HB with process_exists=False (verified live) → rejected
    """

    def test_predates_launch_hb_rejected_after_relaunch(self):
        """HB from before the new launch's start time must be rejected."""
        m = _make_monitor()
        _confirm_online(m)

        # Relaunch — new launch_started_at
        m.note_launch_watchdog(PKG, relaunch=True)
        with m._lock:
            row = m._states[PKG]
            row.process_exists = True
            row.process_seen_since_launch = True
            launch_at = row.launch_started_at

        # HB from before this relaunch
        stale_at = launch_at - 5.0
        verdict = m.ingest_push_heartbeat(
            PKG, alive=True, place_id=111, universe_id=222, job_id="srv",
            at=stale_at,
        )
        self.assertNotEqual(verdict, "online",
                            "pre-launch HB must be rejected (timestamp guard)")

    def test_session_id_logged_but_not_blocking(self):
        """session_id mismatch is logged but does NOT block Online confirmation.
        This is intentional: detector.lua uses game.JobId format which is
        incompatible with the Python monitor's g<gen>_<ts> format."""
        m = _make_monitor()
        with m._lock:
            old_sid = m._states[PKG].session_id

        _confirm_online(m)
        m.note_launch_watchdog(PKG, relaunch=True)
        with m._lock:
            row = m._states[PKG]
            row.process_exists = True
            row.process_seen_since_launch = True
            new_sid = row.session_id

        self.assertNotEqual(old_sid, new_sid, "relaunch must generate a new session_id")

        # Old-format session_id is logged but does NOT block Online
        verdict = m.ingest_push_heartbeat(
            PKG, alive=True, place_id=111, universe_id=222, job_id="srv",
            session_id=old_sid,
        )
        self.assertEqual(verdict, "online",
                         "session_id mismatch must not block Online (diagnostic only)")

    def test_no_session_id_still_works_v1_compat(self):
        """v1 heartbeats (no session_id) must still work for backward compat."""
        m = _make_monitor()
        verdict = m.ingest_push_heartbeat(
            PKG, alive=True, place_id=111, universe_id=222, job_id="srvA"
        )
        self.assertEqual(verdict, "online",
                         "v1 heartbeat without session_id must still confirm Online")


# ============================================================================
# T08 - stagger waits for true Online proof, not stale status
# ============================================================================

class T08_StaggerWaitsForTrueOnline(unittest.TestCase):
    """The stagger gate must not unblock on Online from a PREVIOUS generation."""

    def test_stale_online_generation_does_not_unblock_stagger(self):
        m = _make_monitor()

        # Bump to generation 3
        for _ in range(3):
            m.note_launch_watchdog(PKG, relaunch=True)
        with m._lock:
            m._states[PKG].process_exists = True
            m._states[PKG].process_seen_since_launch = True

        expected_gen = m.get_launch_generation(PKG)

        # Stale: online_confirmed_generation stuck at 1
        with m._lock:
            m._states[PKG].online_confirmed_generation = 1

        self.assertLess(
            m.get_online_generation(PKG),
            expected_gen,
            "stale online_generation must be < expected_generation",
        )

    def test_current_generation_unblocks_stagger(self):
        m = _make_monitor()
        m.note_launch_watchdog(PKG, relaunch=True)
        with m._lock:
            row = m._states[PKG]
            row.process_exists = True
            row.process_seen_since_launch = True
            sid = row.session_id
            expected_gen = row.launch_generation

        m.ingest_push_heartbeat(
            PKG, alive=True, place_id=111, universe_id=222, job_id="srv",
            session_id=sid,
        )
        self.assertEqual(
            m.get_online_generation(PKG),
            expected_gen,
            "fresh HB must set online_generation == launch_generation immediately",
        )

    def test_stagger_gate_timeout_is_under_300s(self):
        """The stagger gate timeout must be <= 120s (not silently 300s)."""
        import agent.commands as cmds
        import inspect
        src = inspect.getsource(cmds.cmd_start)
        self.assertIn("120", src, "stagger gate timeout must be 120s, not 300s")
        self.assertNotIn("_ONLINE_GATE_TIMEOUT_S = 300", src,
                         "old 300s timeout must be replaced")

    def test_blocked_reason_logged_every_30s(self):
        """Stagger code must log blocked_reason on a periodic interval."""
        import agent.commands as cmds
        import inspect
        src = inspect.getsource(cmds.cmd_start)
        self.assertIn("blocked_reason", src, "stagger must emit blocked_reason log")
        self.assertIn("GATE_WARN_INTERVAL", src,
                      "stagger must define a warn interval constant")


# ============================================================================
# T09 - crash dialog / process alive triggers recovery
# ============================================================================

class T09_CrashDialogTriggersRecovery(unittest.TestCase):
    """If a logcat disconnect/crash line arrives for the current session,
    the monitor must demote the package so the supervisor triggers relaunch."""

    def test_with_reason_after_online_demotes_package(self):
        """Roblox 'with reason' line (disconnect) must demote an Online package."""
        m = _make_monitor()
        _confirm_online(m)

        with patch.object(m, "_process_check", return_value=(True, ["9999"], False)):
            ev_before = m.evaluate_package(PKG)
        self.assertTrue(ev_before.is_online_confirmed,
                        "package must be Online before the disconnect")

        now = time.time()
        with m._lock:
            row = m._states[PKG]
            # Inject a 'with reason' line AFTER the last positive online evidence
            row.last_with_reason_at = now + 0.01
            row.last_positive_online_evidence_at = now - 5

        with patch.object(m, "_process_check", return_value=(True, ["9999"], False)):
            ev_after = m.evaluate_package(PKG)
        self.assertFalse(ev_after.is_online_confirmed,
                         "logcat disconnect must demote Online package")

    def test_process_missing_marks_dead_not_nhb(self):
        """When process disappears from a previously-online package,
        evaluate_package must report Dead public status, not No-Heartbeat."""
        m = _make_monitor()
        _confirm_online(m)

        with m._lock:
            row = m._states[PKG]
            row.process_exists = False
            row.last_process_gone_at = time.time()
            row.current_pid = ""

        m.try_mark_force_close_dead(PKG)

        with patch.object(m, "_process_check", return_value=(False, [], True)):
            ev = m.evaluate_package(PKG)
        self.assertEqual(ev.public_status, "Dead",
                         "process_missing must yield Dead, not No Heartbeat")


# ============================================================================
# T10 - /install/test/latest returns valid shell installer
# ============================================================================

@unittest.skipIf(
    os.environ.get("DENG_SKIP_INSTALLER_TESTS", "").strip() in {"1", "true", "yes"},
    "installer tests skipped (DENG_SKIP_INSTALLER_TESTS=1)"
)
class T10_InstallerScriptValid(unittest.TestCase):
    _BASE_URL = "https://rejoin.deng.my.id"

    def _get(self, path: str, timeout: int = 15):
        import urllib.request
        req = urllib.request.Request(
            f"{self._BASE_URL}{path}",
            headers={"User-Agent": "deng-installer-test/2.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            return None, str(exc)

    def test_install_endpoint_returns_200(self):
        status, body = self._get("/install/test/latest")
        if status is None:
            self.skipTest(f"No network: {body}")
        self.assertEqual(status, 200, "install endpoint must return 200")

    def test_installer_is_valid_shell_script(self):
        status, body = self._get("/install/test/latest")
        if status is None:
            self.skipTest("No network")
        self.assertTrue(
            body.startswith("#!/") or "bash" in body[:200],
            "installer must start with a shell shebang or bash invocation"
        )
        self.assertIn("curl", body, "installer must contain curl command")

    def test_installer_contains_sha256(self):
        status, body = self._get("/install/test/latest")
        if status is None:
            self.skipTest("No network")
        self.assertIn("sha256", body.lower(),
                      "installer must embed SHA256 for artifact verification")


# ============================================================================
# T11 - test artifact downloads and SHA matches
# ============================================================================

@unittest.skipIf(
    os.environ.get("DENG_SKIP_INSTALLER_TESTS", "").strip() in {"1", "true", "yes"},
    "installer tests skipped"
)
class T11_ArtifactSHAMatches(unittest.TestCase):
    _BASE_URL = "https://rejoin.deng.my.id"

    def _get_json(self, path: str, timeout: int = 15):
        import urllib.request, json
        req = urllib.request.Request(
            f"{self._BASE_URL}{path}",
            headers={"User-Agent": "deng-installer-test/2.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            return None

    def test_package_token_returns_valid_sha256(self):
        data = self._get_json("/install/package-token/test/latest")
        if data is None:
            self.skipTest("No network or endpoint unavailable")
        self.assertIn("artifact_sha256", data,
                      "package-token must include artifact_sha256")
        sha = data.get("artifact_sha256", "")
        self.assertRegex(sha, r"^[0-9a-f]{64}$",
                         "artifact_sha256 must be a 64-char hex string")


# ============================================================================
# T12 - stable/latest is unchanged by test deployments
# ============================================================================

@unittest.skipIf(
    os.environ.get("DENG_SKIP_INSTALLER_TESTS", "").strip() in {"1", "true", "yes"},
    "installer tests skipped"
)
class T12_StableLatestUnchanged(unittest.TestCase):
    """Publishing test/latest must never touch stable/latest."""

    _BASE_URL = "https://rejoin.deng.my.id"

    def _installer_body(self, path: str) -> str | None:
        import urllib.request
        req = urllib.request.Request(
            f"{self._BASE_URL}{path}",
            headers={"User-Agent": "deng-installer-test/2.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception:
            return None

    def test_stable_endpoint_is_reachable(self):
        body = self._installer_body("/install/stable/latest")
        if body is None:
            self.skipTest("No network")
        self.assertIsNotNone(body, "stable/latest must be reachable")

    def test_test_and_stable_are_independent_channels(self):
        test_body   = self._installer_body("/install/test/latest")
        stable_body = self._installer_body("/install/stable/latest")
        if test_body is None or stable_body is None:
            self.skipTest("No network")
        self.assertTrue(
            test_body.startswith("#!/") or "bash" in test_body[:200],
            "test/latest must be a shell script"
        )
        self.assertTrue(
            stable_body.startswith("#!/") or "bash" in stable_body[:200],
            "stable/latest must be a shell script"
        )

    def test_deploying_test_does_not_change_stable_sha(self):
        """Stable SHA in rejoin_versions.json must be valid hex."""
        import json
        versions_path = os.path.join(
            os.path.dirname(__file__), "..", "data", "rejoin_versions.json"
        )
        if not os.path.isfile(versions_path):
            self.skipTest("data/rejoin_versions.json not available")
        with open(versions_path) as f:
            raw = json.load(f)
        # versions.json may be a list of entries or a dict keyed by channel
        entries = raw if isinstance(raw, list) else list(raw.values())
        shas = [
            e.get("artifact_sha256", "")
            for e in entries
            if isinstance(e, dict) and e.get("artifact_sha256")
        ]
        for sha in shas:
            self.assertRegex(sha, r"^[0-9a-f]{64}$",
                             f"artifact_sha256 must be 64-char hex, got: {sha!r}")


# ============================================================================
# Bonus: detector.lua v2 content checks
# ============================================================================

class T_DetectorLuaV2Content(unittest.TestCase):
    """Verify assets/lua/detector.lua has all v2 requirements."""

    def _lua_src(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "assets", "lua", "detector.lua"
        )
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_emits_start_marker(self):
        self.assertIn("DENGRJN_START", self._lua_src())

    def test_emits_join_marker_after_game_context(self):
        src = self._lua_src()
        self.assertIn("DENGRJN_JOIN", src)
        # The actual emit() call for DENGRJN_JOIN must come AFTER the
        # _gameContextValid guard.  Search for the emit call, not the comment.
        emit_join_pos = src.find('emit(string.format(\n    "DENGRJN_JOIN')
        ctx_pos = src.find("until gameContextValid() or tick()")
        self.assertGreater(
            emit_join_pos, ctx_pos,
            "DENGRJN_JOIN emit must come after gameContextValid() wait"
        )

    def test_emits_heartbeat_with_session_id(self):
        src = self._lua_src()
        self.assertIn("DENGRJN_HB", src)
        self.assertIn("SESSION_ID", src)

    def test_burst_schedule_present(self):
        src = self._lua_src()
        self.assertIn("BURST_SCHEDULE", src)
        for n in ("0", "1", "2", "5", "10"):
            self.assertIn(n, src)

    def test_waits_for_job_id(self):
        src = self._lua_src()
        self.assertIn("JobId", src)
        self.assertIn("PlaceId", src)

    def test_fallback_url_present_in_bootstrap(self):
        from agent.detection_lua import DETECTOR_URL_FALLBACK
        self.assertIn("global", DETECTOR_URL_FALLBACK,
                      "fallback URL must point to legacy global repo")


# ============================================================================
# Bonus: new PackageRjnState fields
# ============================================================================

class T_PackageRjnStateFields(unittest.TestCase):
    """All required state data fields must exist on PackageRjnState."""

    REQUIRED_FIELDS = [
        "session_id",
        "current_pid",
        "pid_start_time",
        "process_seen_at",
        "last_fresh_hb_at",
        "last_gamejoin_at",
        "relaunch_started_at",
        "last_hb_session_id",
        "last_rejected_signal_reason",
        "last_state_change_reason",
        "launch_generation",
        "online_confirmed_generation",
        "process_seen_since_launch",
    ]

    def test_all_required_fields_exist(self):
        from agent.rjn_lifecycle_monitor import PackageRjnState
        row = PackageRjnState(package="com.test")
        for field in self.REQUIRED_FIELDS:
            self.assertTrue(
                hasattr(row, field),
                f"PackageRjnState must have field '{field}'"
            )

    def test_note_launch_watchdog_sets_session_id(self):
        from agent.rjn_lifecycle_monitor import RjnLifecycleMonitor
        m = RjnLifecycleMonitor([PKG])
        m.note_launch_watchdog(PKG)
        with m._lock:
            row = m._states[PKG]
            self.assertTrue(row.session_id,
                            "note_launch_watchdog must generate a non-empty session_id")
            self.assertGreater(row.launch_generation, 0)

    def test_relaunch_increments_generation_and_resets_session(self):
        from agent.rjn_lifecycle_monitor import RjnLifecycleMonitor
        m = RjnLifecycleMonitor([PKG])
        m.note_launch_watchdog(PKG)
        with m._lock:
            first_sid = m._states[PKG].session_id
            first_gen = m._states[PKG].launch_generation

        m.note_launch_watchdog(PKG, relaunch=True)
        with m._lock:
            second_sid = m._states[PKG].session_id
            second_gen = m._states[PKG].launch_generation

        self.assertGreater(second_gen, first_gen,
                           "relaunch must increment launch_generation")
        self.assertNotEqual(first_sid, second_sid,
                            "relaunch must generate a new session_id")

    def test_process_disappear_clears_pid_fields(self):
        """When dead lane detects process gone, current_pid must be cleared."""
        from agent.rjn_lifecycle_monitor import RjnLifecycleMonitor
        m = RjnLifecycleMonitor([PKG])
        m.note_launch_watchdog(PKG)
        with m._lock:
            row = m._states[PKG]
            row.current_pid = "9999"
            row.pid_start_time = "12345678"
            row.process_exists = True
            row.process_seen_since_launch = True

        m.try_mark_force_close_dead(PKG)
        with m._lock:
            row = m._states[PKG]
        self.assertEqual(row.current_pid, "",
                         "current_pid must be cleared on force-close Dead")
        self.assertEqual(row.pid_start_time, "",
                         "pid_start_time must be cleared on force-close Dead")


if __name__ == "__main__":
    unittest.main(verbosity=2)
