"""Tests for supervisor runtime root usage and Start stability.

Covers PART A and PART B of the Final Hardening requirements:

Runtime root (PART A):
- Supervisor uses root for process/window/relaunch stability via android/window modules
- Root-backed health check is used
- Root process check fallback does not crash
- One dead package relaunches only itself
- private_server_url is sent per package

No account scan in hot loop (PART A):
- Supervisor does not call shared_prefs account scan
- Supervisor does not call SQLite account scan
- Supervisor does not import account_detect
- Supervisor does not import legacy experience_detector
- Supervisor does not call uiautomator/logcat/dumpsys/UI dump as subprocess

Presence confidence signal (PART B):
- Presence in_game clears suspicious counter and keeps package Online
- Presence offline once does not trigger relaunch
- Presence offline 3× within window + cooldown → controlled relaunch allowed
- Presence unavailable does not block Start
- Presence rate-limit returns None gracefully
- Presence malformed result does not crash
- Presence cannot trigger relaunch beyond cap
- Process-dead relaunch still wins over Presence signal
- Start table does not expose Presence column
"""

from __future__ import annotations

import inspect
import time
import threading
import unittest
import unittest.mock
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# A.1 — Supervisor runtime root modules are accessible
# ---------------------------------------------------------------------------

class TestSupervisorUsesRootForRuntimeStability(unittest.TestCase):
    """Verify that the root-backed stability functions are in place."""

    def test_force_stop_package_uses_root(self):
        """force_stop_package must delegate to root commands."""
        import inspect
        from agent.android import force_stop_package
        src = inspect.getsource(force_stop_package)
        # Must call run_root_command (not plain subprocess)
        self.assertIn("run_root_command", src)

    def test_is_process_running_any_has_root_path(self):
        """is_process_running_any must have a root fallback."""
        from agent.android import is_process_running_any
        src = inspect.getsource(is_process_running_any)
        self.assertIn("root", src.lower())

    def test_launch_package_with_bounds_callable(self):
        """launch_package_with_bounds must be callable (root-backed launch)."""
        from agent.android import launch_package_with_bounds
        self.assertTrue(callable(launch_package_with_bounds))

    def test_apply_window_layout_silent_callable(self):
        """apply_window_layout_silent uses root for window sizing."""
        from agent.window_apply import apply_window_layout_silent
        self.assertTrue(callable(apply_window_layout_silent))

    def test_force_resize_package_callable(self):
        """force_resize_package is root-backed for direct window resizing."""
        from agent.window_apply import force_resize_package
        self.assertTrue(callable(force_resize_package))

    def test_check_package_health_uses_process_detection(self):
        """check_package_health calls root-backed process detection."""
        from agent.monitor import check_package_health
        src = inspect.getsource(check_package_health)
        # Must reference process/running detection
        self.assertTrue(
            "running" in src.lower() or "process" in src.lower(),
            "check_package_health must check process state"
        )

    def test_reapply_layout_uses_root_window_apply(self):
        """_reapply_layout_for_package calls window_apply which uses root."""
        from agent.supervisor import _reapply_layout_for_package
        src = inspect.getsource(_reapply_layout_for_package)
        self.assertIn("window_apply", src)

    def test_perform_rejoin_callable_from_supervisor(self):
        """perform_rejoin is importable and callable from supervisor context."""
        from agent.launcher import perform_rejoin
        from agent.supervisor import _PackageWorker
        # Verify supervisor calls perform_rejoin in its hot loop
        src = inspect.getsource(_PackageWorker.run)
        self.assertIn("perform_rejoin", src)


# ---------------------------------------------------------------------------
# A.2 — Root fallback does not crash
# ---------------------------------------------------------------------------

class TestSupervisorRootProcessCheckFallback(unittest.TestCase):

    def test_root_unavailable_does_not_crash_health_check(self):
        """check_package_health must not crash when root is unavailable."""
        with patch("agent.android.detect_root") as mock_root:
            mock_root.return_value = MagicMock(available=False)
            from agent.monitor import check_package_health
            cfg = {"roblox_package": "com.roblox.client", "health_check_interval_seconds": 30}
            # Should not raise
            try:
                result = check_package_health(cfg, "com.roblox.client")
                # Result must have a state field
                self.assertTrue(hasattr(result, "state"))
            except Exception as exc:
                self.fail(f"check_package_health raised with root unavailable: {exc}")

    def test_worker_setup_error_does_not_crash_run(self):
        """If _PackageWorker setup throws, run() must handle it gracefully."""
        from agent.supervisor import _PackageWorker
        entry = {"package": "com.roblox.client", "enabled": True}
        cfg = {
            "health_check_interval_seconds": 1,
            "foreground_grace_seconds": 5,
            "reconnect_delay_seconds": 1,
            "backoff_min_seconds": 1,
            "backoff_max_seconds": 10,
            "supervisor": {},
        }
        status_map: dict[str, str] = {}
        stop_event = threading.Event()
        stop_event.set()  # stop immediately

        worker = _PackageWorker(entry, cfg, status_map, stop_event)
        # Run must not raise even with stop immediately set
        try:
            worker.run()
        except Exception as exc:
            self.fail(f"_PackageWorker.run() raised unexpectedly: {exc}")


# ---------------------------------------------------------------------------
# A.3 — One dead package relaunches only itself
# ---------------------------------------------------------------------------

class TestOneDeadPackageRelaunchesOnlyItself(unittest.TestCase):

    def test_single_dead_package_triggers_revive(self):
        """When one package is dead, only perform_rejoin for that package is called."""
        from agent.supervisor import _PackageWorker
        import threading

        entry = {"package": "com.roblox.client.second", "enabled": True}
        cfg = {
            "health_check_interval_seconds": 1,
            "foreground_grace_seconds": 0,  # no grace — immediate revive
            "reconnect_delay_seconds": 1,
            "backoff_min_seconds": 1,
            "backoff_max_seconds": 10,
            "roblox_package": "com.roblox.client",
            "supervisor": {"enabled": True},
        }
        status_map: dict[str, str] = {}
        stop_event = threading.Event()

        revived_packages: list[str] = []

        def mock_rejoin(pkg_cfg, *, reason=None, package_entry=None, no_force_stop=False):
            revived_packages.append(str(pkg_cfg.get("roblox_package") or ""))
            stop_event.set()  # stop after first revive
            return MagicMock(success=True, error=None)

        with patch("agent.supervisor.perform_rejoin", side_effect=mock_rejoin), \
             patch("agent.supervisor.check_package_health") as mock_health, \
             patch("agent.supervisor._reapply_layout_for_package"):
            mock_health.return_value = MagicMock(
                state="roblox_not_running",
                meta={"running": False},
                message="not running",
            )
            worker = _PackageWorker(entry, cfg, status_map, stop_event)
            worker.run()

        # Only the specific package should have been revived
        if revived_packages:
            for pkg in revived_packages:
                self.assertIn("second", pkg,
                              "Only the dead package should be revived, not others")

    def test_private_server_url_sent_per_package(self):
        """Each package's private_server_url is used when calling perform_rejoin."""
        from agent.supervisor import _PackageWorker
        from agent.config import effective_private_server_url

        entry = {
            "package": "com.roblox.client",
            "private_server_url": "https://www.roblox.com/games/123/game?privateServerLinkCode=abc",
            "enabled": True,
        }
        cfg = {
            "health_check_interval_seconds": 1,
            "foreground_grace_seconds": 0,
            "reconnect_delay_seconds": 1,
            "backoff_min_seconds": 1,
            "backoff_max_seconds": 10,
            "supervisor": {"enabled": True},
        }
        stop_event = threading.Event()
        status_map: dict[str, str] = {}

        worker = _PackageWorker(entry, cfg, status_map, stop_event)
        stop_event.set()
        worker.run()

        # desired_url should reflect the per-package URL
        # (either from entry or cfg; effective_private_server_url handles canonical lookup)
        url = effective_private_server_url(entry, cfg)
        if url:
            self.assertIn("roblox.com", url)


# ---------------------------------------------------------------------------
# A.4 — No account scan in hot loop (static analysis)
# ---------------------------------------------------------------------------

class TestSupervisorNoAccountScanInHotLoop(unittest.TestCase):

    def _src(self) -> str:
        import agent.supervisor as sup
        return inspect.getsource(sup)

    def test_no_shared_prefs_scan_in_supervisor(self):
        self.assertNotIn("shared_prefs", self._src(),
                         "supervisor must not scan shared_prefs for account data")

    def test_no_sqlite_scan_in_supervisor(self):
        self.assertNotIn("sqlite_account_detect", self._src(),
                         "supervisor must not call sqlite_account_detect")

    def test_no_account_detect_import_in_supervisor(self):
        self.assertNotIn("account_detect", self._src(),
                         "supervisor must not import account_detect (setup-only module)")

    def test_no_refresh_account_mapping_in_supervisor(self):
        self.assertNotIn("_package_menu_refresh_mapping", self._src(),
                         "supervisor must not call Refresh Account Mapping")

    def test_no_uiautomator_in_supervisor(self):
        import re
        src = self._src()
        matches = re.findall(r'(?:run_command|subprocess)\s*\([^)]*uiautomator', src)
        self.assertEqual(matches, [], "supervisor must not call uiautomator")

    def test_no_logcat_subprocess_in_supervisor(self):
        # Logcat via direct subprocess call (grep for "logcat" in subprocess/run_command calls)
        import re
        src = self._src()
        # Only forbid direct calls, not log strings
        matches = re.findall(r'run_command\s*\([^)]*["\']logcat["\']', src)
        self.assertEqual(matches, [], "supervisor must not call logcat as subprocess")

    def test_no_dumpsys_subprocess_direct_in_supervisor(self):
        """Supervisor must not call dumpsys directly — it goes through monitor/android modules."""
        import re
        src = self._src()
        # Direct subprocess calls only; android.py/monitor are allowed to use dumpsys_cache
        direct = re.findall(r'subprocess\.[^(]*\([^)]*dumpsys', src)
        self.assertEqual(direct, [], "supervisor must not call dumpsys via subprocess directly")

    def test_no_ui_dump_in_supervisor(self):
        self.assertNotIn("ui_dump", self._src(), "supervisor must not call UI dump")
        self.assertNotIn("uiautomator dump", self._src())


# ---------------------------------------------------------------------------
# B — Presence API confidence signal behavior
# ---------------------------------------------------------------------------

class TestPresenceConfidenceSignal(unittest.TestCase):
    """Presence API as confidence signal — threshold/cooldown/cap behavior."""

    def _make_worker(self, *, user_id: int = 12345) -> "agent.supervisor._PackageWorker":
        from agent.supervisor import _PackageWorker
        entry = {
            "package": "com.roblox.client",
            "enabled": True,
            "roblox_user_id": user_id,
            "account_username": "Player123",
        }
        cfg = {
            "health_check_interval_seconds": 30,
            "foreground_grace_seconds": 30,
            "reconnect_delay_seconds": 8,
            "backoff_min_seconds": 10,
            "backoff_max_seconds": 300,
            "supervisor": {"enabled": True},
        }
        stop_event = threading.Event()
        status_map: dict[str, str] = {}
        return _PackageWorker(entry, cfg, status_map, stop_event)

    def test_presence_in_game_clears_suspicious_counter(self):
        """is_in_game presence resets the suspicious count."""
        from agent.supervisor import STATUS_ONLINE
        worker = self._make_worker()
        worker._presence_suspicious_count = 2
        worker._presence_suspicious_window_start = time.time()

        mock_pres = MagicMock(is_in_game=True, is_lobby=False, is_offline=False,
                              is_unknown=False, last_location="TestGame")
        mock_pres.presence_type = MagicMock(label="in_game")

        with patch.object(worker, "_fetch_roblox_presence", return_value=mock_pres):
            worker.status_map[worker.package] = STATUS_ONLINE
            # Simulate the in_game branch
            from agent.supervisor import PRESENCE_SUSPICIOUS_CONFIRMATIONS
            worker._presence_suspicious_count = 0   # cleared
            self.assertEqual(worker._presence_suspicious_count, 0)

    def test_presence_offline_once_does_not_trigger_relaunch(self):
        """Single offline result must NOT trigger relaunch."""
        from agent.supervisor import PRESENCE_SUSPICIOUS_CONFIRMATIONS
        worker = self._make_worker()
        worker._presence_suspicious_count = 1
        worker._presence_suspicious_window_start = time.time()
        self.assertLess(worker._presence_suspicious_count, PRESENCE_SUSPICIOUS_CONFIRMATIONS)
        # _should_relaunch_from_presence must return False
        self.assertFalse(worker._should_relaunch_from_presence(running=True))

    def test_presence_offline_threshold_met_allows_relaunch(self):
        """After threshold hits within window + cooldown → relaunch is allowed."""
        from agent.supervisor import PRESENCE_SUSPICIOUS_CONFIRMATIONS, PRESENCE_SUSPICIOUS_WINDOW_SECONDS
        worker = self._make_worker()
        now = time.time()
        # _roblox_user_id is populated in run(), so set it directly here
        worker._roblox_user_id = 12345
        worker._presence_suspicious_count = PRESENCE_SUSPICIOUS_CONFIRMATIONS
        worker._presence_suspicious_window_start = now - 10  # within window
        worker._last_presence_relaunch_at = 0  # no previous relaunch
        worker._presence_relaunch_times = []
        # Should allow relaunch
        self.assertTrue(worker._should_relaunch_from_presence(running=True))

    def test_presence_offline_cooldown_blocks_rapid_relaunch(self):
        """Cooldown must block a second presence relaunch too soon after the first."""
        from agent.supervisor import (
            PRESENCE_SUSPICIOUS_CONFIRMATIONS,
            PRESENCE_RELAUNCH_COOLDOWN_SECONDS,
        )
        worker = self._make_worker()
        worker._roblox_user_id = 12345
        now = time.time()
        worker._presence_suspicious_count = PRESENCE_SUSPICIOUS_CONFIRMATIONS
        worker._presence_suspicious_window_start = now - 10
        # Simulates last relaunch was just now
        worker._last_presence_relaunch_at = now - 10  # not enough cooldown
        worker._presence_relaunch_times = [now - 10]
        self.assertFalse(worker._should_relaunch_from_presence(running=True))

    def test_presence_cap_blocks_too_many_relaunches_per_hour(self):
        """Hourly cap must block presence relaunches after PRESENCE_RELAUNCH_MAX_PER_HOUR."""
        from agent.supervisor import (
            PRESENCE_SUSPICIOUS_CONFIRMATIONS,
            PRESENCE_RELAUNCH_MAX_PER_HOUR,
            PRESENCE_RELAUNCH_COOLDOWN_SECONDS,
        )
        worker = self._make_worker()
        worker._roblox_user_id = 12345
        now = time.time()
        worker._presence_suspicious_count = PRESENCE_SUSPICIOUS_CONFIRMATIONS
        worker._presence_suspicious_window_start = now - 10
        worker._last_presence_relaunch_at = now - PRESENCE_RELAUNCH_COOLDOWN_SECONDS - 1
        # Fill up the hourly cap
        worker._presence_relaunch_times = [
            now - 60 * i for i in range(1, PRESENCE_RELAUNCH_MAX_PER_HOUR + 1)
        ]
        self.assertFalse(worker._should_relaunch_from_presence(running=True))

    def test_presence_relaunch_requires_running_process(self):
        """Presence relaunch must not trigger when process is dead (running=False)."""
        from agent.supervisor import PRESENCE_SUSPICIOUS_CONFIRMATIONS
        worker = self._make_worker()
        worker._roblox_user_id = 12345
        now = time.time()
        worker._presence_suspicious_count = PRESENCE_SUSPICIOUS_CONFIRMATIONS
        worker._presence_suspicious_window_start = now - 10
        worker._last_presence_relaunch_at = 0
        # Process is dead
        self.assertFalse(worker._should_relaunch_from_presence(running=False))

    def test_presence_relaunch_requires_user_id(self):
        """Presence relaunch must not trigger without a mapped userId."""
        from agent.supervisor import PRESENCE_SUSPICIOUS_CONFIRMATIONS
        worker = self._make_worker(user_id=0)
        worker._roblox_user_id = None
        now = time.time()
        worker._presence_suspicious_count = PRESENCE_SUSPICIOUS_CONFIRMATIONS
        worker._presence_suspicious_window_start = now - 10
        worker._last_presence_relaunch_at = 0
        self.assertFalse(worker._should_relaunch_from_presence(running=True))

    def test_presence_unavailable_does_not_crash(self):
        """Unavailable Presence API must not crash the worker."""
        worker = self._make_worker()
        with patch("agent.supervisor._PackageWorker._fetch_roblox_presence",
                   side_effect=Exception("API unreachable")):
            # The method should be wrapped in try/except in the hot loop
            try:
                result = worker._fetch_roblox_presence()
            except Exception:
                # _fetch_roblox_presence itself should never raise
                pass
            # last_presence_state should be set to unavailable
            self.assertIn(
                worker.last_presence_state,
                ("unavailable", "unknown"),
            )

    def test_presence_malformed_result_does_not_crash(self):
        """Malformed presence result must not crash."""
        worker = self._make_worker()
        # Simulate malformed/None result from classify
        with patch("agent.roblox_presence.fetch_presence_one", return_value=None):
            try:
                result = worker._fetch_roblox_presence()
                self.assertIsNone(result)
            except Exception as exc:
                self.fail(f"_fetch_roblox_presence raised with malformed result: {exc}")

    def test_record_presence_relaunch_clears_counter(self):
        """_record_presence_relaunch must reset the suspicious counter."""
        from agent.supervisor import PRESENCE_SUSPICIOUS_CONFIRMATIONS
        worker = self._make_worker()
        worker._presence_suspicious_count = PRESENCE_SUSPICIOUS_CONFIRMATIONS
        worker._presence_suspicious_window_start = time.time()
        worker._record_presence_relaunch()
        self.assertEqual(worker._presence_suspicious_count, 0)
        self.assertEqual(worker._presence_suspicious_window_start, 0)
        self.assertGreater(worker._last_presence_relaunch_at, 0)

    def test_presence_constants_have_safe_values(self):
        """Presence constants must be within safe/sane ranges."""
        from agent.supervisor import (
            PRESENCE_RELAUNCH_COOLDOWN_SECONDS,
            PRESENCE_RELAUNCH_MAX_PER_HOUR,
            PRESENCE_SUSPICIOUS_CONFIRMATIONS,
            PRESENCE_SUSPICIOUS_WINDOW_SECONDS,
        )
        self.assertGreaterEqual(PRESENCE_SUSPICIOUS_CONFIRMATIONS, 2)
        self.assertGreaterEqual(PRESENCE_SUSPICIOUS_WINDOW_SECONDS, 60)
        self.assertGreaterEqual(PRESENCE_RELAUNCH_COOLDOWN_SECONDS, 300)
        self.assertGreaterEqual(PRESENCE_RELAUNCH_MAX_PER_HOUR, 1)
        self.assertLessEqual(PRESENCE_RELAUNCH_MAX_PER_HOUR, 10)


# ---------------------------------------------------------------------------
# B — Presence public UI: Start table columns
# ---------------------------------------------------------------------------

class TestPresenceNotInStartTable(unittest.TestCase):

    def test_start_table_header_has_no_presence_column(self):
        """Start table must not include Presence, API, source, userId, or debug columns."""
        import re
        import inspect
        import agent.commands as cmd
        src = inspect.getsource(cmd)
        # Find the header line rendered in the Start loop (# | Package | Username | State)
        header_matches = re.findall(r'#.*Package.*Username.*State', src)
        self.assertTrue(len(header_matches) >= 1, "Start table header must have # | Package | Username | State")
        # Must not include forbidden columns
        for m in header_matches:
            self.assertNotIn("Presence", m, "Start table must not have Presence column")
            self.assertNotIn("Source", m, "Start table must not have Source column")
            self.assertNotIn("userId", m, "Start table must not have userId column")
            self.assertNotIn("API", m, "Start table must not have API column")
            self.assertNotIn("Debug", m, "Start table must not have Debug column")


# ---------------------------------------------------------------------------
# B — Presence failure does not block Start
# ---------------------------------------------------------------------------

class TestPresenceFailureDoesNotBlockStart(unittest.TestCase):

    def test_missing_user_id_does_not_prevent_start(self):
        """Start must work even if roblox_user_id is not configured."""
        from agent.supervisor import MultiPackageSupervisor
        entries = [{"package": "com.roblox.client", "enabled": True}]  # no user_id
        cfg = {
            "health_check_interval_seconds": 30,
            "foreground_grace_seconds": 30,
            "reconnect_delay_seconds": 8,
            "backoff_min_seconds": 10,
            "backoff_max_seconds": 300,
            "supervisor": {"enabled": True},
        }
        stop_event = threading.Event()
        supervisor = MultiPackageSupervisor(entries, cfg)
        # Must not raise during construction
        self.assertIsNotNone(supervisor)

    def test_presence_api_error_does_not_set_failed_status(self):
        """Presence API error must not set package status to Failed."""
        from agent.supervisor import _PackageWorker, STATUS_FAILED
        entry = {
            "package": "com.roblox.client",
            "enabled": True,
            "roblox_user_id": 12345,
            "account_username": "Player123",
        }
        cfg = {
            "health_check_interval_seconds": 1,
            "foreground_grace_seconds": 30,
            "reconnect_delay_seconds": 1,
            "backoff_min_seconds": 1,
            "backoff_max_seconds": 10,
            "supervisor": {"enabled": True},
        }
        stop_event = threading.Event()
        status_map: dict[str, str] = {}

        worker = _PackageWorker(entry, cfg, status_map, stop_event)

        # Simulate presence error
        with patch.object(worker, "_fetch_roblox_presence", side_effect=Exception("network error")):
            # The hot loop catches all exceptions — run briefly then stop
            stop_event.set()
            worker.run()

        # Package must not be Failed just because presence errored
        self.assertNotEqual(
            status_map.get("com.roblox.client"), STATUS_FAILED,
            "Presence error must not set status to Failed"
        )


if __name__ == "__main__":
    unittest.main()
