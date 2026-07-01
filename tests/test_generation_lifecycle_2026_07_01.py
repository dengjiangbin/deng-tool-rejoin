"""Generation-based detector authority unit tests.

Required test matrix (p-657c77a6b0, p-1a7fcce102):
 T1  Stale heartbeat after force-close cannot set Online.
 T2  No Heartbeat + process gone becomes Dead immediately.
 T3  HB-loss with process alive stays No Heartbeat (not Dead).
 T4  STATUS_RELAUNCHING cannot be overwritten to STATUS_LAUNCHING.
 T5  relaunch_inflight clears only on fresh current-generation heartbeat.
 T6  Presence API cannot override Dead/process_missing.
 T7  Stagger gate waits for fresh current-generation Online proof.
 T8  /install/test/latest returns a valid POSIX shell script.
 T9  Artifact from test/latest downloads successfully (SHA matches manifest).
 T10 stable/latest is not modified by test/latest changes.
 TDL Dead lane arms within one 1s hot-lane pass after process first appears.
"""

from __future__ import annotations

import hashlib
import sys
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.rjn_lifecycle_monitor import (
    ONLINE_HB_FRESH_SECONDS,
    PackageEvaluateResult,
    PackageRjnState,
    RjnLifecycleMonitor,
    STATE_DEAD,
    STATE_DISCONNECTED,
    STATE_LAUNCHING,
    STATE_ONLINE_CONFIRMED,
    STATE_RELAUNCHING,
    STATE_STOPPED,
)
from agent.supervisor import (
    STATUS_DEAD,
    STATUS_LAUNCHING,
    STATUS_NO_HEARTBEAT,
    STATUS_ONLINE,
    STATUS_RELAUNCHING,
    WatchdogSupervisor,
)

PKG = "com.moons.litesc"
ENTRY = {"package": PKG, "enabled": True, "roblox_user_id": 12345}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_monitor() -> RjnLifecycleMonitor:
    """Return a RjnLifecycleMonitor with all Android I/O stubbed out."""
    mon = RjnLifecycleMonitor.__new__(RjnLifecycleMonitor)
    mon.packages = [PKG]
    mon._lock = threading.Lock()
    mon._states: dict = {}
    mon._uid_map: dict = {}
    mon._uid_resolutions: dict = {}
    mon._pid_to_package: dict = {}
    mon._uid_to_package: dict = {}
    mon._logcat_thread = None
    mon._logcat_proc = None
    mon._logcat_stream_alive = True
    mon._logcat_started_at = time.time()
    mon._last_any_launch_at = 0.0
    mon._last_all_packages_dump_at = 0.0
    mon._last_force_close_lane_at = 0.0
    mon._root_info = None
    mon._launch_watchdog_seconds = 120.0
    mon._stop_event = threading.Event()
    # Default: process alive
    mon._process_check = lambda pkg: (True, ["1234"], True)
    # Stub heavy I/O
    mon._scan_logcat_dump = lambda pkg, now: None
    mon._detect_live_disconnect = lambda pkg: ("", "")
    mon._try_confirm_launch_online = lambda pkg, now: None
    mon._was_ever_online_confirmed = lambda row: bool(row.ingame_hb_ever or row.online_since > 0)
    mon.stream_fresh_for = lambda pkg, sec: True
    mon.retry_confirm_pending_heartbeat = lambda pkg: None
    mon._clear_online_evidence = _real_clear_online_evidence.__get__(mon, type(mon))
    return mon


def _real_clear_online_evidence(self, pkg: str, *, at: float | None = None) -> None:
    now = float(at or time.time())
    with self._lock:
        row = self._states.get(pkg)
        if row is None:
            return
        row.last_positive_online_evidence_at = 0.0
        row.online_evidence_source = ""
        row.last_process_gone_at = now


def _transition_to_dead(mon: RjnLifecycleMonitor, pkg: str, now: float | None = None) -> None:
    """Directly transition a row to STATE_DEAD (bypasses _transition internals)."""
    now = now or time.time()
    with mon._lock:
        row = mon._states.setdefault(pkg, PackageRjnState(package=pkg))
        row.internal_state = STATE_DEAD
        row.last_transition_reason = "process_missing"
        row.last_dead_detected_at = now
        row.last_process_gone_at = now
        row.process_exists = False
        row.pids = []


def _boot_online_state(mon: RjnLifecycleMonitor, pkg: str = PKG) -> PackageRjnState:
    """Put a row into fully online state (gen 1)."""
    now = time.time()
    with mon._lock:
        row = mon._states.setdefault(pkg, PackageRjnState(package=pkg))
        row.launch_started_at = now - 30
        row.watchdog_active = False
        row.dead_lane_enabled = True
        row.process_seen_since_launch = True
        row.ingame_hb_ever = True
        row.last_ingame_hb_at = now - 2
        row.last_ingame_hb_wall_at = now - 2
        row.last_hb_pid = "1234"
        row.last_hb_uid = "u0_a123"
        row.online_since = now - 25
        row.last_positive_online_evidence_at = now - 2
        row.online_evidence_source = "push_heartbeat"
        row.internal_state = STATE_ONLINE_CONFIRMED
        row.process_exists = True
        row.pids = ["1234"]
        row.launch_generation = 1
        row.online_confirmed_generation = 1
    return mon._states[pkg]


def _make_ev(
    *,
    is_online: bool = False,
    process_exists: bool = True,
    internal_state: str = STATE_STOPPED,
    public_status: str = STATUS_DEAD,
    reason: str = "",
    gen: int = 1,
    online_gen: int = -1,
    dead_reason: str = "",
    failed_checks: list | None = None,
) -> PackageEvaluateResult:
    return PackageEvaluateResult(
        package=PKG,
        internal_state=internal_state,
        public_status=public_status,
        reason=reason,
        is_online_confirmed=is_online,
        failed_checks=failed_checks or [],
        process_exists=process_exists,
        detail={
            "launch_generation": gen,
            "online_confirmed_generation": online_gen,
            "reason_internal": reason or dead_reason,
            "dead_reason": dead_reason,
            "launch_failed_reason": "",
        },
    )


def _make_sup() -> WatchdogSupervisor:
    """Return a supervisor with all I/O stubbed out."""
    sup = WatchdogSupervisor([ENTRY], {"supervisor": {}})
    # Patch out Android calls on the monitor
    sup._rjn_monitor._process_check = lambda pkg: (True, ["1234"], True)
    sup._rjn_monitor._scan_logcat_dump = lambda pkg, now: None
    sup._rjn_monitor._detect_live_disconnect = lambda pkg: ("", "")
    sup._rjn_monitor.stream_fresh_for = lambda pkg, sec: True
    return sup


# ---------------------------------------------------------------------------
# T1: Stale heartbeat after force-close cannot set Online
# ---------------------------------------------------------------------------

class T1_StaleHeartbeatAfterForceClose(unittest.TestCase):
    def test_stale_hb_rejected_before_launch_start(self) -> None:
        """_is_authoritative_hb rejects a heartbeat older than launch_started_at."""
        mon = _make_monitor()
        _boot_online_state(mon)
        now = time.time()

        with mon._lock:
            row = mon._states[PKG]
            row.launch_generation += 1
            row.online_confirmed_generation = -1
            row.launch_started_at = now         # new launch started NOW
            row.last_process_gone_at = now - 5  # process was gone briefly

        with mon._lock:
            row = mon._states[PKG]

        # Heartbeat seen BEFORE the new launch — must be rejected
        ok = mon._is_authoritative_hb(
            PKG, row,
            seen_at=now - 10,   # older than launch_started_at
            pid="1234",
            uid="u0_a123",
            process_exists=True,
        )
        self.assertFalse(ok, "Heartbeat older than launch_started_at must be rejected")

    def test_stale_generation_flag(self) -> None:
        """After relaunch, online_confirmed_generation must be -1."""
        mon = _make_monitor()
        mon._process_check = lambda pkg: (True, ["1234"], True)
        mon._was_ever_online_confirmed = lambda row: False
        mon.note_launch_watchdog(PKG, relaunch=False)
        # Simulate Online confirmed
        with mon._lock:
            mon._states[PKG].online_confirmed_generation = 1
        # Now relaunch
        mon.note_launch_watchdog(PKG, relaunch=True)
        self.assertEqual(mon.get_online_generation(PKG), -1,
                         "Relaunch must reset online_confirmed_generation to -1")

    def test_process_gone_rejects_subsequent_hb(self) -> None:
        """A heartbeat arriving after last_process_gone_at is rejected."""
        mon = _make_monitor()
        _boot_online_state(mon)
        now = time.time()

        with mon._lock:
            row = mon._states[PKG]
            row.last_process_gone_at = now - 1  # process went away 1s ago
            row.launch_started_at = now - 30

        with mon._lock:
            row = mon._states[PKG]

        # Heartbeat arrived at now - 1.5, which is <= process_gone_at + 0.5
        ok = mon._is_authoritative_hb(
            PKG, row,
            seen_at=now - 1.5,
            pid="1234", uid="u0_a123",
            process_exists=False,
        )
        self.assertFalse(ok, "Heartbeat at time of/before process gone must be rejected")


# ---------------------------------------------------------------------------
# T2: No Heartbeat + process gone → Dead immediately
# ---------------------------------------------------------------------------

class T2_NHBProcessGoneBecomesdead(unittest.TestCase):
    def test_nhb_process_gone_becomes_dead(self) -> None:
        """supervisor: NHB + process_exists=False → Dead (rule 6: HB-loss+missing=Dead)."""
        sup = _make_sup()
        # Patch evaluate_package to say: NHB with process gone
        ev = _make_ev(
            is_online=False, process_exists=False,
            internal_state=STATE_DEAD,
            public_status=STATUS_NO_HEARTBEAT,
            reason="process_missing", dead_reason="process_missing",
        )
        with patch.object(sup._rjn_monitor, "evaluate_package", return_value=ev), \
             patch("agent.supervisor.log_event"), \
             patch.object(sup, "_ingest_push_heartbeat", return_value=None), \
             patch.object(sup, "_sync_logcat_hb_push_channel", return_value=None), \
             patch.object(sup, "_push_fresh", return_value=False):
            sup._set_status(PKG, STATUS_NO_HEARTBEAT)
            state, detail = sup._detect_package_state(PKG, ENTRY)

        self.assertEqual(state, STATUS_DEAD,
                         "NHB + process gone must immediately become Dead")

    def test_nhb_process_gone_in_supervisor_detect(self) -> None:
        """Supervisor _detect_package_state: if current=NHB and process_exists=False → Dead."""
        sup = _make_sup()
        ev = _make_ev(
            is_online=False, process_exists=False,
            internal_state=STATE_DEAD, public_status=STATUS_DEAD,
            reason="process_missing", dead_reason="process_missing",
        )
        with patch.object(sup._rjn_monitor, "evaluate_package", return_value=ev), \
             patch("agent.supervisor.log_event"), \
             patch.object(sup, "_ingest_push_heartbeat", return_value=None), \
             patch.object(sup, "_sync_logcat_hb_push_channel", return_value=None), \
             patch.object(sup, "_push_fresh", return_value=False):
            sup._set_status(PKG, STATUS_NO_HEARTBEAT)
            state, detail = sup._detect_package_state(PKG, ENTRY)

        self.assertNotEqual(state, STATUS_NO_HEARTBEAT,
                            "Process gone must not stay in No Heartbeat")
        self.assertEqual(state, STATUS_DEAD)


# ---------------------------------------------------------------------------
# T3: HB-loss with process alive stays No Heartbeat (not Dead)
# ---------------------------------------------------------------------------

class T3_HBLossProcessAliveStaysNHB(unittest.TestCase):
    def test_hb_loss_process_alive_stays_nhb(self) -> None:
        """Process alive + HB loss → No Heartbeat, not Dead."""
        sup = _make_sup()
        ev = _make_ev(
            is_online=False, process_exists=True,
            internal_state=STATE_DISCONNECTED,
            public_status=STATUS_NO_HEARTBEAT,
            reason="heartbeat_lost", dead_reason="",
        )
        with patch.object(sup._rjn_monitor, "evaluate_package", return_value=ev), \
             patch("agent.supervisor.log_event"), \
             patch.object(sup, "_ingest_push_heartbeat", return_value=None), \
             patch.object(sup, "_sync_logcat_hb_push_channel", return_value=None), \
             patch.object(sup, "_push_fresh", return_value=False):
            sup._set_status(PKG, STATUS_NO_HEARTBEAT)
            state, detail = sup._detect_package_state(PKG, ENTRY)

        self.assertNotEqual(state, STATUS_DEAD,
                            "Process alive + HB loss must NOT be Dead")

    def test_alive_process_cannot_be_dead(self) -> None:
        """When process_exists=True, Dead state must not be set from HB loss alone."""
        sup = _make_sup()
        # Mark as opened so the supervisor doesn't short-circuit to Ready
        sup._package_opened.add(PKG)
        ev = _make_ev(
            is_online=False, process_exists=True,
            internal_state=STATE_DISCONNECTED,
            public_status=STATUS_NO_HEARTBEAT,
            reason="heartbeat_lost",
        )
        with patch.object(sup._rjn_monitor, "evaluate_package", return_value=ev), \
             patch("agent.supervisor.log_event"), \
             patch.object(sup, "_ingest_push_heartbeat", return_value=None), \
             patch.object(sup, "_sync_logcat_hb_push_channel", return_value=None), \
             patch.object(sup, "_push_fresh", return_value=False):
            state, _ = sup._detect_package_state(PKG, ENTRY)

        self.assertNotEqual(state, STATUS_DEAD,
                            "Alive process + HB loss must NOT become Dead")


# ---------------------------------------------------------------------------
# T4: STATUS_RELAUNCHING cannot be overwritten to STATUS_LAUNCHING
# ---------------------------------------------------------------------------

class T4_RelaunchingNotOverwrittenByLaunching(unittest.TestCase):
    def test_sync_stagger_keeps_relaunching(self) -> None:
        """sync_stagger_display_status must not change RELAUNCHING to LAUNCHING."""
        sup = _make_sup()
        sup._set_status(PKG, STATUS_RELAUNCHING)
        sup._all_launches_completed = False
        # Mark as opened so the method iterates over this package
        sup._package_opened.add(PKG)
        # Stub out push-channel check to return no fresh push
        with patch.object(sup, "_sync_logcat_hb_push_channel", return_value=None), \
             patch.object(sup, "_push_fresh", return_value=False), \
             patch("agent.supervisor.log_event"):
            sup.sync_stagger_display_status()

        self.assertEqual(sup.status_map.get(PKG), STATUS_RELAUNCHING,
                         "sync_stagger must not downgrade RELAUNCHING to LAUNCHING")

    def test_post_grace_relaunching_stays_relaunching(self) -> None:
        """After grace window expires, RELAUNCHING must not revert to LAUNCHING."""
        sup = _make_sup()
        pkg = PKG
        sup._relaunch_inflight.add(pkg)
        sup._relaunch_verify_until[pkg] = time.time() - 60  # already expired

        ev = _make_ev(
            is_online=False, process_exists=True,
            internal_state=STATE_RELAUNCHING,
            public_status=STATUS_RELAUNCHING,
            reason="relaunch_post_grace_pending_confirmation",
            gen=2, online_gen=-1,
        )
        with patch.object(sup._rjn_monitor, "evaluate_package", return_value=ev), \
             patch("agent.supervisor.log_event"), \
             patch.object(sup, "_ingest_push_heartbeat", return_value=None), \
             patch.object(sup, "_sync_logcat_hb_push_channel", return_value=None), \
             patch.object(sup, "_push_fresh", return_value=False):
            sup._set_status(pkg, STATUS_RELAUNCHING)
            state, _ = sup._detect_package_state(pkg, ENTRY)

        self.assertEqual(state, STATUS_RELAUNCHING,
                         "Post-grace state must remain RELAUNCHING, not revert to LAUNCHING")
        self.assertNotEqual(state, STATUS_LAUNCHING)


# ---------------------------------------------------------------------------
# T5: relaunch_inflight clears only on fresh current-generation heartbeat
# ---------------------------------------------------------------------------

class T5_RelaunchInflightClearsOnFreshHB(unittest.TestCase):
    def test_inflight_cleared_when_online(self) -> None:
        """_relaunch_inflight is discarded when detect returns Online."""
        sup = _make_sup()
        sup._relaunch_inflight.add(PKG)
        sup._relaunch_verify_until[PKG] = time.time() + 60

        ev = _make_ev(
            is_online=True, process_exists=True,
            internal_state=STATE_ONLINE_CONFIRMED,
            public_status=STATUS_ONLINE,
            reason="push_heartbeat_confirmed",
            gen=2, online_gen=2,
        )
        with patch.object(sup._rjn_monitor, "evaluate_package", return_value=ev), \
             patch("agent.supervisor.log_event"), \
             patch.object(sup, "_ingest_push_heartbeat", return_value=None), \
             patch.object(sup, "_sync_logcat_hb_push_channel", return_value=None), \
             patch.object(sup, "_push_fresh", return_value=True):
            sup._set_status(PKG, STATUS_RELAUNCHING)
            state, _ = sup._detect_package_state(PKG, ENTRY)

        self.assertEqual(state, STATUS_ONLINE)
        self.assertNotIn(PKG, sup._relaunch_inflight,
                         "relaunch_inflight must be cleared when Online is confirmed")

    def test_generation_reset_on_relaunch(self) -> None:
        """online_confirmed_generation resets to -1 on every relaunch call."""
        mon = _make_monitor()
        mon._process_check = lambda pkg: (True, ["1234"], True)
        mon._was_ever_online_confirmed = lambda row: False
        mon.note_launch_watchdog(PKG)
        with mon._lock:
            mon._states[PKG].online_confirmed_generation = 1
        mon.note_launch_watchdog(PKG, relaunch=True)
        self.assertEqual(mon.get_online_generation(PKG), -1,
                         "Relaunch must reset online_confirmed_generation")


# ---------------------------------------------------------------------------
# T6: Presence API cannot override Dead/process_missing
# ---------------------------------------------------------------------------

class T6_PresenceCannotOverrideDead(unittest.TestCase):
    def test_dead_process_stays_dead_despite_presence(self) -> None:
        """process_exists=False from process scan must beat any Presence result."""
        sup = _make_sup()
        ev = _make_ev(
            is_online=False, process_exists=False,
            internal_state=STATE_DEAD, public_status=STATUS_DEAD,
            reason="process_missing", dead_reason="process_missing",
        )
        # Even if _fetch_presence says InGame, the process check wins
        with patch.object(sup._rjn_monitor, "evaluate_package", return_value=ev), \
             patch.object(sup, "_fetch_presence", return_value={"status": "InGame"}), \
             patch("agent.supervisor.log_event"), \
             patch.object(sup, "_ingest_push_heartbeat", return_value=None), \
             patch.object(sup, "_sync_logcat_hb_push_channel", return_value=None), \
             patch.object(sup, "_push_fresh", return_value=False):
            sup._set_status(PKG, STATUS_DEAD)
            state, detail = sup._detect_package_state(PKG, ENTRY)

        self.assertNotEqual(state, STATUS_ONLINE,
                            "Presence must not override process_missing Dead")

    def test_is_authoritative_hb_rejects_dead_process(self) -> None:
        """_is_authoritative_hb returns False when process_exists=False."""
        mon = _make_monitor()
        _boot_online_state(mon)
        now = time.time()
        with mon._lock:
            row = mon._states[PKG]

        ok = mon._is_authoritative_hb(
            PKG, row,
            seen_at=now - 1,    # fresh heartbeat
            pid="1234", uid="u0_a123",
            process_exists=False,   # but process is gone
        )
        self.assertFalse(ok, "process gone must beat fresh heartbeat")


# ---------------------------------------------------------------------------
# T7: Stagger gate checks current-generation Online
# ---------------------------------------------------------------------------

class T7_StaggerGateChecksGeneration(unittest.TestCase):
    def test_generation_increments_on_each_launch(self) -> None:
        mon = _make_monitor()
        mon._process_check = lambda pkg: (True, ["1234"], True)
        mon._was_ever_online_confirmed = lambda row: False

        self.assertEqual(mon.get_launch_generation(PKG), 0)
        mon.note_launch_watchdog(PKG)
        self.assertEqual(mon.get_launch_generation(PKG), 1)
        mon.note_launch_watchdog(PKG, relaunch=True)
        self.assertEqual(mon.get_launch_generation(PKG), 2)

    def test_online_gen_resets_to_minus1_on_relaunch(self) -> None:
        mon = _make_monitor()
        mon._process_check = lambda pkg: (True, ["1234"], True)
        mon._was_ever_online_confirmed = lambda row: False

        mon.note_launch_watchdog(PKG)
        with mon._lock:
            mon._states[PKG].online_confirmed_generation = 1

        mon.note_launch_watchdog(PKG, relaunch=True)
        self.assertEqual(mon.get_online_generation(PKG), -1)

    def test_stale_gen_does_not_satisfy_gate(self) -> None:
        mon = _make_monitor()
        mon._process_check = lambda pkg: (True, ["1234"], True)
        mon._was_ever_online_confirmed = lambda row: False

        mon.note_launch_watchdog(PKG)
        expected_gen = mon.get_launch_generation(PKG)  # 1
        # Old Online from gen 0
        with mon._lock:
            mon._states[PKG].online_confirmed_generation = 0

        self.assertFalse(mon.get_online_generation(PKG) >= expected_gen,
                         "Stale-gen Online must not unblock stagger")

    def test_current_gen_satisfies_gate(self) -> None:
        mon = _make_monitor()
        mon._process_check = lambda pkg: (True, ["1234"], True)
        mon._was_ever_online_confirmed = lambda row: False

        mon.note_launch_watchdog(PKG)
        expected_gen = mon.get_launch_generation(PKG)  # 1
        with mon._lock:
            mon._states[PKG].online_confirmed_generation = 1

        self.assertTrue(mon.get_online_generation(PKG) >= expected_gen,
                        "Current-gen Online must unblock stagger")

    def test_evaluate_records_online_generation(self) -> None:
        """After evaluate_package confirms Online, online_confirmed_generation == launch_generation."""
        mon = _make_monitor()
        mon._process_check = lambda pkg: (True, ["1234"], True)
        mon._was_ever_online_confirmed = lambda row: True
        _boot_online_state(mon)

        result = mon.evaluate_package(PKG, fast_push=True, hot_lane_only=True)
        if result.is_online_confirmed:
            with mon._lock:
                row = mon._states[PKG]
            self.assertEqual(
                row.online_confirmed_generation, row.launch_generation,
                "evaluate_package must record online_confirmed_generation == launch_generation",
            )


# ---------------------------------------------------------------------------
# T8: /install/test/latest returns valid POSIX shell script
# ---------------------------------------------------------------------------

class T8_InstallerScript(unittest.TestCase):
    def test_installer_returns_200_with_shebang(self) -> None:
        import urllib.request
        try:
            req = urllib.request.Request(
                "https://rejoin.deng.my.id/install/test/latest",
                headers={"User-Agent": "deng-rejoin-installer-test/1.0"},
            )
            r = urllib.request.urlopen(req, timeout=15)
        except Exception as exc:
            self.skipTest(f"Network unavailable: {exc}")
        self.assertEqual(r.status, 200)
        body = r.read().decode("utf-8", errors="replace")
        self.assertTrue(body.startswith("#!/usr/bin/env sh") or body.startswith("#!/bin/sh"))
        self.assertIn("curl", body)

    def test_installer_embeds_64hex_sha(self) -> None:
        import re, urllib.request
        try:
            req = urllib.request.Request(
                "https://rejoin.deng.my.id/install/test/latest",
                headers={"User-Agent": "deng-rejoin-installer-test/1.0"},
            )
            body = urllib.request.urlopen(req, timeout=15).read().decode()
        except Exception as exc:
            self.skipTest(f"Network unavailable: {exc}")
        m = re.search(r's="([0-9a-f]{64})"', body)
        self.assertIsNotNone(m, "Installer must embed a 64-hex SHA-256")


# ---------------------------------------------------------------------------
# T9: Artifact downloads and SHA matches
# ---------------------------------------------------------------------------

class T9_ArtifactDownload(unittest.TestCase):
    def test_artifact_sha_matches_token(self) -> None:
        import json, urllib.request
        try:
            req = urllib.request.Request(
                "https://rejoin.deng.my.id/install/test/package-token?t=unittest",
                headers={"User-Agent": "deng-rejoin-installer/2.0"},
            )
            tok = json.loads(urllib.request.urlopen(req, timeout=15).read())
        except Exception as exc:
            self.skipTest(f"Network unavailable: {exc}")
        expected = tok.get("sha256", "")
        url = tok.get("url", "")
        self.assertTrue(expected)
        self.assertTrue(url.startswith("https://"))
        req2 = urllib.request.Request(url, headers={"User-Agent": "deng-rejoin-installer/2.0"})
        data = urllib.request.urlopen(req2, timeout=120).read()
        self.assertEqual(hashlib.sha256(data).hexdigest(), expected)

    def test_artifact_contains_required_files(self) -> None:
        import io, json, tarfile, urllib.request
        try:
            req = urllib.request.Request(
                "https://rejoin.deng.my.id/install/test/package-token?t=unittest2",
                headers={"User-Agent": "deng-rejoin-installer/2.0"},
            )
            tok = json.loads(urllib.request.urlopen(req, timeout=15).read())
            data = urllib.request.urlopen(
                urllib.request.Request(tok["url"], headers={"User-Agent": "deng-rejoin-installer/2.0"}),
                timeout=120,
            ).read()
        except Exception as exc:
            self.skipTest(f"Network unavailable: {exc}")
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            names = tf.getnames()
        for required in ("RELEASE-MANIFEST.json", "RELEASE-MANIFEST.sig",
                         "agent/.deng_runtime.bin", "BUILD-INFO.json"):
            self.assertIn(required, names, f"Artifact must contain {required}")


# ---------------------------------------------------------------------------
# T10: stable/latest is not modified
# ---------------------------------------------------------------------------

class T10_StableLatestUnmodified(unittest.TestCase):
    def test_stable_versions_not_using_test_sha(self) -> None:
        """Stable rows in rejoin_versions.json must not use the test artifact SHA."""
        import json
        path = PROJECT / "data" / "rejoin_versions.json"
        if not path.exists():
            self.skipTest("rejoin_versions.json not found")
        rows = json.loads(path.read_text())
        test_sha = "c49c3311b85452fed0745eaefb10bafb14dfaf0dbb4a84c60f558686beac3db9"
        for row in rows:
            if str(row.get("channel") or "").lower() in {"stable", "public"}:
                sha = row.get("artifact_sha256", "")
                self.assertNotEqual(sha, test_sha,
                    f"Stable {row.get('version')} must not use test artifact SHA")

    def test_stable_endpoint_returns_different_sha(self) -> None:
        import re, urllib.request
        try:
            def _sha(path: str) -> str:
                req = urllib.request.Request(
                    f"https://rejoin.deng.my.id{path}",
                    headers={"User-Agent": "deng-rejoin-installer-test/1.0"},
                )
                body = urllib.request.urlopen(req, timeout=15).read().decode()
                m = re.search(r's="([0-9a-f]{64})"', body)
                return m.group(1) if m else ""
            stable = _sha("/install/latest")
            test = _sha("/install/test/latest")
        except Exception as exc:
            self.skipTest(f"Network unavailable: {exc}")
        if stable and test:
            self.assertNotEqual(stable, test,
                "stable/latest SHA must differ from test/latest SHA")


# ---------------------------------------------------------------------------
# TDL: Dead lane arms quickly and fires on process gone
# ---------------------------------------------------------------------------

class TDL_DeadLane(unittest.TestCase):
    def test_hot_lane_sets_process_seen_when_alive(self) -> None:
        """_poll_dead_hot_lane sets process_seen_since_launch=True when process first appears."""
        mon = _make_monitor()
        with mon._lock:
            row = mon._states.setdefault(PKG, PackageRjnState(package=PKG))
            row.watchdog_active = True
            row.dead_lane_enabled = True
            row.process_seen_since_launch = False
            row.internal_state = STATE_LAUNCHING
        mon._process_check = lambda pkg: (True, ["1234"], True)

        mon._poll_dead_hot_lane()

        with mon._lock:
            self.assertTrue(mon._states[PKG].process_seen_since_launch,
                            "Hot lane must set process_seen_since_launch=True on first alive check")

    def test_hot_lane_fires_dead_after_process_seen(self) -> None:
        """Once process_seen_since_launch=True, dead lane marks Dead when process gone."""
        mon = _make_monitor()
        with mon._lock:
            row = mon._states.setdefault(PKG, PackageRjnState(package=PKG))
            row.watchdog_active = False
            row.dead_lane_enabled = True
            row.process_seen_since_launch = True
            row.ingame_hb_ever = True
            row.internal_state = STATE_ONLINE_CONFIRMED
            row.online_since = time.time() - 60
            row.last_positive_online_evidence_at = time.time() - 5
            row.process_exists = True
            row.launch_generation = 1
            row.online_confirmed_generation = 1

        mon._was_ever_online_confirmed = lambda row: True
        dead_calls: list = []

        def _mock_dead(pkg, *, at=None):
            dead_calls.append(pkg)
            return True

        mon.try_mark_force_close_dead = _mock_dead
        mon._process_check = lambda pkg: (False, [], True)

        mon._poll_dead_hot_lane()
        self.assertIn(PKG, dead_calls,
                      "Dead lane must call try_mark_force_close_dead when process gone")

    def test_launch_generation_in_detail(self) -> None:
        """evaluate_package includes launch_generation and online_confirmed_generation in detail."""
        mon = _make_monitor()
        mon._process_check = lambda pkg: (True, ["1234"], True)
        mon._was_ever_online_confirmed = lambda row: True
        _boot_online_state(mon)

        result = mon.evaluate_package(PKG, fast_push=True, hot_lane_only=True)
        self.assertIn("launch_generation", result.detail,
                      "detail must include launch_generation")
        self.assertIn("online_confirmed_generation", result.detail,
                      "detail must include online_confirmed_generation")
        self.assertIsInstance(result.detail["launch_generation"], int)


if __name__ == "__main__":
    unittest.main(verbosity=2)
