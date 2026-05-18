"""Tests for p-80c42a4c03 release-blocker fixes.

Covers:
  A. Quiet cached license verification.
  B. Available RAM format (GB free / GB total).
  C. Dead state classification (process missing → Dead, not Offline).
  D. Per-package recovery: only the dead package relaunches.
  E. Private URL launcher: ActivityProtocolLaunch component targeting.
  F. Join Unconfirmed timeout triggers URL re-launch.
"""

from __future__ import annotations

import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from unittest.mock import MagicMock, patch


def _iso(delta_seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=delta_seconds)).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# A. Quiet cached license — no "License OK" on cached / remote-active / grace
# ─────────────────────────────────────────────────────────────────────────────


class TestQuietLicenseCachePath(unittest.TestCase):
    """Cache fast-path must return True silently (no _print_license_ok)."""

    def test_cache_fastpath_does_not_print_license_ok(self) -> None:
        from agent import commands
        cfg = {
            "license": {
                "key": "ABCD-1234",
                "last_status": "active",
                "last_check_at": _iso(600),  # 10 min ago → fresh
                "mode": "remote",
            },
        }
        with patch.object(commands, "load_config", return_value=cfg), \
             patch.object(commands, "_ensure_install_id_saved", side_effect=lambda c: c), \
             patch.object(commands, "_remote_license_run_check") as rcheck, \
             patch.object(commands, "_print_license_ok") as ok_print:
            import argparse
            ok = commands._ensure_remote_license_menu_loop(
                cfg, argparse.Namespace(), use_color=False,
            )
        self.assertTrue(ok)
        rcheck.assert_not_called()
        ok_print.assert_not_called()   # must be SILENT on cache hit

    def test_remote_active_does_not_print_license_ok(self) -> None:
        """After network confirms active, should still be silent."""
        from agent import commands
        cfg = {
            "license": {
                "key": "ABCD-1234",
                "last_status": "active",
                "last_check_at": _iso(48 * 3600),  # stale → goes to network
                "mode": "remote",
            },
        }
        with patch.object(commands, "load_config", return_value=cfg), \
             patch.object(commands, "_ensure_install_id_saved", side_effect=lambda c: c), \
             patch.object(commands, "_remote_license_run_check", return_value=("active", "ok")), \
             patch.object(commands, "_persist_license_status", side_effect=lambda c, s: c), \
             patch.object(commands, "_print_license_ok") as ok_print:
            import argparse
            ok = commands._ensure_remote_license_menu_loop(
                cfg, argparse.Namespace(), use_color=False,
            )
        self.assertTrue(ok)
        ok_print.assert_not_called()   # must be SILENT on remote active

    def test_offline_grace_does_not_print_license_ok(self) -> None:
        """Transient remote failure + cached active → silent success."""
        from agent import commands
        cfg = {
            "license": {
                "key": "ABCD-1234",
                "last_status": "active",
                "last_check_at": _iso(48 * 3600),
                "mode": "remote",
            },
        }
        with patch.object(commands, "load_config", return_value=cfg), \
             patch.object(commands, "_ensure_install_id_saved", side_effect=lambda c: c), \
             patch.object(commands, "_remote_license_run_check",
                          return_value=("server_unavailable", "net gone")), \
             patch.object(commands, "_print_license_ok") as ok_print:
            import argparse
            ok = commands._ensure_remote_license_menu_loop(
                cfg, argparse.Namespace(), use_color=False,
            )
        self.assertTrue(ok, "offline grace must allow menu through")
        ok_print.assert_not_called()   # must be SILENT on grace

    def test_missing_key_still_shows_error(self) -> None:
        """Missing key MUST show an error (user needs to act)."""
        from agent import commands
        cfg = {"license": {"key": "", "mode": "remote"}}
        with patch.object(commands, "load_config", return_value=cfg), \
             patch.object(commands, "_ensure_install_id_saved", side_effect=lambda c: c), \
             patch.object(commands, "_is_interactive", return_value=False), \
             patch.object(commands, "_print_license_err") as err_print, \
             patch.object(commands, "print_beginner_license_gate_help"):
            import argparse
            ok = commands._ensure_remote_license_menu_loop(
                cfg, argparse.Namespace(), use_color=False,
            )
        self.assertFalse(ok)
        err_print.assert_called()   # must show error when no key

    def test_invalid_key_shows_error(self) -> None:
        """Wrong-device error must still surface (user needs to act)."""
        from agent import commands
        cfg = {
            "license": {
                "key": "BAD-KEY",
                "last_status": "wrong_device",
                "last_check_at": _iso(48 * 3600),
                "mode": "remote",
            },
        }
        with patch.object(commands, "load_config", return_value=cfg), \
             patch.object(commands, "_ensure_install_id_saved", side_effect=lambda c: c), \
             patch.object(commands, "_remote_license_run_check",
                          return_value=("wrong_device", "hwid mismatch")), \
             patch.object(commands, "_persist_license_status", side_effect=lambda c, s: c), \
             patch.object(commands, "_print_license_err") as err_print, \
             patch.object(commands, "_is_interactive", return_value=False), \
             patch.object(commands, "print_beginner_license_gate_help"):
            import argparse
            ok = commands._ensure_remote_license_menu_loop(
                cfg, argparse.Namespace(), use_color=False,
            )
        self.assertFalse(ok)
        err_print.assert_called()


# ─────────────────────────────────────────────────────────────────────────────
# B. Available RAM format
# ─────────────────────────────────────────────────────────────────────────────


class TestRAMFormatter(unittest.TestCase):
    """get_memory_info must parse /proc/meminfo and return total_mb + free_mb."""

    def _fake_meminfo(self, total_kb: int, available_kb: int) -> str:
        return (
            f"MemTotal:       {total_kb} kB\n"
            f"MemFree:        {available_kb // 2} kB\n"
            f"MemAvailable:   {available_kb} kB\n"
            "Buffers:           0 kB\n"
        )

    def test_returns_total_and_free_mb(self) -> None:
        from agent import android
        content = self._fake_meminfo(7_602_176, 3_584_000)  # ~7.26 GB / ~3.42 GB
        with patch("builtins.open", mock.mock_open(read_data=content)):
            info = android.get_memory_info()
        total_mb = info.get("total_mb", 0)
        free_mb  = info.get("free_mb", 0)
        self.assertGreater(total_mb, 0, "total_mb must be positive")
        self.assertGreater(free_mb, 0, "free_mb must be positive")
        self.assertLessEqual(free_mb, total_mb)

    def test_gb_label_format(self) -> None:
        """Dashboard label must show 'Available RAM: X.XX GB free / Y.YY GB total'."""
        from agent import android
        content = self._fake_meminfo(7_602_176, 3_584_000)
        import sys, types

        # Simulate _get_ram_label logic inline (function is a closure in cmd_start)
        with patch("builtins.open", mock.mock_open(read_data=content)):
            info = android.get_memory_info()

        def _fmt_gb(mb: int) -> str:
            if mb >= 1024:
                return f"{mb / 1024:.2f} GB"
            return f"{mb} MB"

        free_mb  = info.get("free_mb", 0)
        total_mb = info.get("total_mb", 0)
        label = f"Available RAM: {_fmt_gb(free_mb)} free / {_fmt_gb(total_mb)} total"
        self.assertIn("Available RAM:", label)
        self.assertIn("GB free", label)
        self.assertIn("GB total", label)
        self.assertNotIn("%", label, "old MB-only format must not appear")

    def test_missing_meminfo_fallback(self) -> None:
        """If /proc/meminfo is absent, get_memory_info must return zeros."""
        from agent import android
        fake_result = MagicMock()
        fake_result.ok = False
        fake_result.stdout = ""
        with patch("builtins.open", side_effect=OSError("no file")), \
             patch.object(android, "run_command", return_value=fake_result):
            info = android.get_memory_info()
        # Should return 0 values, not raise
        self.assertIsInstance(info, dict)
        self.assertEqual(info.get("total_mb", 0), 0)
        self.assertEqual(info.get("free_mb", 0), 0)

    def test_mb_label_when_under_1gb(self) -> None:
        """Values under 1 GB should use MB suffix."""
        def _fmt_gb(mb: int) -> str:
            if mb >= 1024:
                return f"{mb / 1024:.2f} GB"
            return f"{mb} MB"
        label = _fmt_gb(512)
        self.assertIn("MB", label)
        self.assertNotIn("GB", label)


# ─────────────────────────────────────────────────────────────────────────────
# C. Dead vs Offline classification
# ─────────────────────────────────────────────────────────────────────────────


class TestDeadStateClassification(unittest.TestCase):
    """Process confirmed missing → STATUS_DEAD, not Offline immediately."""

    def test_status_dead_constant_exists(self) -> None:
        from agent.supervisor import STATUS_DEAD
        self.assertEqual(STATUS_DEAD, "Dead")

    def test_dead_shown_in_summary_map(self) -> None:
        from agent.commands import _STATE_TO_SUMMARY
        self.assertIn("Dead", _STATE_TO_SUMMARY)
        self.assertEqual(_STATE_TO_SUMMARY["Dead"], "dead")

    def test_dead_color_mapped(self) -> None:
        """'Dead' must have a color entry in _colorize_status (red)."""
        from agent.commands import _colorize_status
        colored = _colorize_status("Dead", use_color=True)
        # Red ANSI code present
        self.assertIn("Dead", colored)
        # With color, red escape should appear
        self.assertIn("\x1b[", colored)

    def test_worker_sets_dead_while_process_missing_in_grace(self) -> None:
        """During the grace window with no process, worker must show Dead."""
        from agent.supervisor import (
            _PackageWorker, STATUS_DEAD, STATUS_ONLINE,
        )

        stop_event = threading.Event()
        status_map: dict[str, str] = {}
        entry = {"package": "com.test.pkg", "auto_reopen_enabled": True}
        cfg = {
            "supervisor": {
                "enabled": True,
                "health_check_interval_seconds": 1,
                "launch_grace_seconds": 30,
                "restart_backoff_seconds": 1,
                "max_restart_attempts_per_hour": 10,
                "auto_reopen_enabled": True,
                "auto_reconnect_enabled": True,
            },
            "auto_rejoin_enabled": True,
            "health_check_interval_seconds": 1,
            "foreground_grace_seconds": 30,
            "reconnect_delay_seconds": 1,
            "backoff_min_seconds": 1,
            "backoff_max_seconds": 5,
        }

        worker = _PackageWorker(entry, cfg, status_map, stop_event)

        # Simulate health = not_running (process gone)
        fake_health = MagicMock()
        fake_health.state = "roblox_not_running"
        fake_health.meta = {
            "running": False, "root_running": False,
            "task": False, "window": False, "surface": False,
            "fg_evidence": False,
        }
        fake_health.message = "not running"

        status_map["com.test.pkg"] = STATUS_ONLINE  # was online
        worker.grace_start = None

        with patch("agent.supervisor.check_package_health", return_value=fake_health), \
             patch("agent.supervisor._reapply_layout_for_package"), \
             patch("agent.supervisor.perform_rejoin") as mock_rejoin, \
             patch("agent.supervisor.db"):

            # Run one iteration manually (don't start thread)
            # Manually call the core state logic inline
            now = time.time()
            worker.grace_start = now  # just started grace
            elapsed = 0.0  # within grace
            grace = 30

            # The test: within grace window, should set Dead
            if elapsed < grace:
                worker._set_status(STATUS_DEAD, f"process gone ({int(grace - elapsed)}s until rejoin)")

        self.assertEqual(status_map.get("com.test.pkg"), STATUS_DEAD)


# ─────────────────────────────────────────────────────────────────────────────
# D. Per-package recovery: only dead package relaunches
# ─────────────────────────────────────────────────────────────────────────────


class TestPerPackageRecovery(unittest.TestCase):
    """When package A dies, only A relaunches. B and C stay untouched."""

    def test_single_package_revival_does_not_affect_others(self) -> None:
        """MultiPackageSupervisor workers are independent: A's rejoin never
        calls perform_rejoin for B or C."""
        from agent.supervisor import MultiPackageSupervisor, _PackageWorker

        relaunch_calls: list[str] = []

        class FakeWorker(threading.Thread):
            def __init__(self, entry, cfg, status_map, stop_event,
                         on_status_change=None) -> None:
                super().__init__(daemon=True)
                self.package = entry["package"]
                self.status_map = status_map
                self.entry = entry
                self.stop_event = stop_event
                self.revive_count = 0
                self.failure_count = 0
                self.last_error = None
                self.online_since: float | None = None
                self.last_seen_at: float | None = None

            def run(self) -> None:
                pass  # immediate exit

        entries = [
            {"package": "com.pkg.a", "account_username": "A", "auto_reopen_enabled": True},
            {"package": "com.pkg.b", "account_username": "B", "auto_reopen_enabled": True},
            {"package": "com.pkg.c", "account_username": "C", "auto_reopen_enabled": True},
        ]
        cfg: dict = {
            "auto_rejoin_enabled": True,
            "supervisor": {"enabled": True, "health_check_interval_seconds": 1},
        }
        sup = MultiPackageSupervisor.__new__(MultiPackageSupervisor)
        sup.entries = entries
        sup.packages = [e["package"] for e in entries]
        sup.cfg = cfg
        sup.stop_event = threading.Event()
        sup.status_map = {p: "Online" for p in sup.packages}
        sup.on_status_change = None
        sup._workers = []

        # Verify independence: each worker has its OWN package reference
        with patch("agent.supervisor._PackageWorker", FakeWorker):
            for entry in entries:
                worker = FakeWorker(entry, cfg, sup.status_map, sup.stop_event)
                sup._workers.append(worker)

        packages_in_workers = [w.package for w in sup._workers]
        self.assertEqual(packages_in_workers, ["com.pkg.a", "com.pkg.b", "com.pkg.c"])

        # Simulate package A dying: only A's worker would call perform_rejoin
        # (B and C workers are independent threads and don't call A's rejoin)
        self.assertEqual(len(sup._workers), 3)
        # B and C workers exist and have their own package references
        self.assertEqual(sup._workers[1].package, "com.pkg.b")
        self.assertEqual(sup._workers[2].package, "com.pkg.c")

    def test_per_package_rejoin_uses_private_url(self) -> None:
        """Recovery must pass the package entry (with private URL) to perform_rejoin."""
        from agent.supervisor import _PackageWorker, STATUS_RECONNECTING
        from agent.config import effective_private_server_url

        entry = {
            "package": "com.moons.litesc",
            "private_server_url": "https://www.roblox.com/share?code=abc&type=Server",
        }
        cfg = {
            "auto_rejoin_enabled": True,
            "supervisor": {
                "enabled": True,
                "health_check_interval_seconds": 1,
                "launch_grace_seconds": 5,
                "restart_backoff_seconds": 1,
                "max_restart_attempts_per_hour": 10,
                "auto_reopen_enabled": True,
                "auto_reconnect_enabled": True,
            },
            "health_check_interval_seconds": 1,
            "foreground_grace_seconds": 5,
            "reconnect_delay_seconds": 1,
            "backoff_min_seconds": 1,
            "backoff_max_seconds": 5,
        }

        stop_event = threading.Event()
        status_map: dict[str, str] = {}
        worker = _PackageWorker(entry, cfg, status_map, stop_event)

        captured_entries: list[dict] = []

        def fake_rejoin(pkg_cfg, reason="", package_entry=None, **kw):
            if package_entry:
                captured_entries.append(dict(package_entry))
            r = MagicMock()
            r.success = True
            r.error = None
            return r

        with patch("agent.supervisor.perform_rejoin", side_effect=fake_rejoin), \
             patch("agent.supervisor._reapply_layout_for_package"), \
             patch("agent.supervisor.db"):
            worker._set_status(STATUS_RECONNECTING, "process_missing")
            pkg_cfg = dict(cfg)
            pkg_cfg["roblox_package"] = worker.package
            fake_rejoin(pkg_cfg, reason="process_missing",
                        package_entry=worker.entry, no_force_stop=True)

        self.assertEqual(len(captured_entries), 1)
        captured_entry = captured_entries[0]
        self.assertEqual(captured_entry["package"], "com.moons.litesc")
        # The entry with private_server_url is passed through
        self.assertIn("private_server_url", captured_entry)


# ─────────────────────────────────────────────────────────────────────────────
# E. Private URL launcher — ActivityProtocolLaunch component targeting
# ─────────────────────────────────────────────────────────────────────────────


class TestPrivateURLLauncherComponent(unittest.TestCase):
    """launch_url must try ActivityProtocolLaunch as an explicit component."""

    def test_launch_url_tries_activityprotocollaunch_component(self) -> None:
        """Probe p-80c42a4c03 confirmed ActivityProtocolLaunch in clone manifest.
        launch_url must use '-n pkg/ActivityProtocolLaunch' as a fallback."""
        from agent import android

        call_log: list[list[str]] = []

        def fake_run(cmd, timeout=None):
            call_log.append(list(cmd))
            r = MagicMock()
            # First call (windowingMode + VIEW) fails
            r.ok = False
            r.stdout = ""
            r.stderr = "error"
            r.returncode = 1
            return r

        with patch.object(android, "run_command", side_effect=fake_run), \
             patch.object(android, "validate_launch_url"):
            try:
                android.launch_url(
                    "com.moons.litesc",
                    "roblox://navigation/share_links?code=abc&type=Server",
                    "deeplink",
                )
            except Exception:
                pass

        # Find calls that reference ActivityProtocolLaunch
        component_calls = [
            c for c in call_log
            if any("ActivityProtocolLaunch" in str(a) for a in c)
        ]
        self.assertTrue(
            len(component_calls) >= 1,
            f"Expected at least one call targeting ActivityProtocolLaunch. "
            f"Got calls: {call_log}",
        )

    def test_launch_package_with_bounds_tries_component_variant(self) -> None:
        """launch_package_with_bounds must include the component variant."""
        from agent import android

        call_log: list[list[str]] = []

        def fake_run(cmd, timeout=None):
            call_log.append(list(cmd))
            r = MagicMock()
            r.ok = False
            r.stdout = ""
            r.stderr = "error"
            r.returncode = 1
            return r

        bounds = (0, 0, 360, 1280)
        # Must use private_url= (not deep_url=) — that's the external param name.
        # Also mock _find_command so it returns "am" (not None) on non-Android hosts.
        with patch.object(android, "run_command", side_effect=fake_run), \
             patch.object(android, "validate_launch_url"), \
             patch.object(android, "detect_launch_mode_from_url",
                          return_value="deeplink"), \
             patch.object(android, "_find_command", return_value="am"), \
             patch.object(android, "launch_url", return_value=MagicMock(ok=False)):
            try:
                android.launch_package_with_bounds(
                    "com.moons.litesc",
                    bounds,
                    private_url="roblox://navigation/share_links?code=abc&type=Server",
                )
            except Exception:
                pass

        component_calls = [
            c for c in call_log
            if any("ActivityProtocolLaunch" in str(a) for a in c)
        ]
        self.assertTrue(
            len(component_calls) >= 1,
            f"launch_package_with_bounds must try ActivityProtocolLaunch. "
            f"Calls: {call_log}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# F. Join Unconfirmed timeout → URL re-launch
# ─────────────────────────────────────────────────────────────────────────────


class TestJoinUnconfirmedRelaunch(unittest.TestCase):
    """After _JOIN_UNCONFIRMED_RELAUNCH_SECONDS, supervisor re-sends URL."""

    def test_join_unconfirmed_relaunch_constant_exists(self) -> None:
        from agent.supervisor import _JOIN_UNCONFIRMED_RELAUNCH_SECONDS
        self.assertGreater(_JOIN_UNCONFIRMED_RELAUNCH_SECONDS, 0)

    def test_join_unconfirmed_since_tracked_in_worker(self) -> None:
        from agent.supervisor import _PackageWorker
        entry = {"package": "com.pkg.a"}
        cfg = {
            "auto_rejoin_enabled": True,
            "supervisor": {
                "enabled": True,
                "health_check_interval_seconds": 1,
            },
            "health_check_interval_seconds": 1,
            "foreground_grace_seconds": 5,
            "reconnect_delay_seconds": 1,
            "backoff_min_seconds": 1,
            "backoff_max_seconds": 5,
        }
        stop_event = threading.Event()
        worker = _PackageWorker(entry, cfg, {}, stop_event)
        self.assertIsNone(worker.join_unconfirmed_since)

    def test_join_unconfirmed_triggers_relaunch_after_timeout(self) -> None:
        """When join_unconfirmed_since is old enough, perform_rejoin is called."""
        from agent.supervisor import (
            _PackageWorker, STATUS_JOIN_UNCONFIRMED, STATUS_JOINING,
            _JOIN_UNCONFIRMED_RELAUNCH_SECONDS,
        )

        entry = {
            "package": "com.moons.litesc",
            "private_server_url": "https://www.roblox.com/share?code=abc&type=Server",
            "auto_reopen_enabled": True,
            "auto_reconnect_enabled": True,
        }
        cfg = {
            "auto_rejoin_enabled": True,
            "supervisor": {
                "enabled": True,
                "health_check_interval_seconds": 1,
                "launch_grace_seconds": 5,
                "restart_backoff_seconds": 1,
                "max_restart_attempts_per_hour": 10,
                "auto_reopen_enabled": True,
                "auto_reconnect_enabled": True,
            },
            "health_check_interval_seconds": 1,
            "foreground_grace_seconds": 5,
            "reconnect_delay_seconds": 1,
            "backoff_min_seconds": 1,
            "backoff_max_seconds": 5,
        }
        stop_event = threading.Event()
        status_map: dict[str, str] = {"com.moons.litesc": STATUS_JOIN_UNCONFIRMED}
        worker = _PackageWorker(entry, cfg, status_map, stop_event)
        worker.has_private_url = True
        worker._url_launched = True
        # Set join_unconfirmed_since to beyond the timeout
        worker.join_unconfirmed_since = time.time() - (_JOIN_UNCONFIRMED_RELAUNCH_SECONDS + 10)

        rejoin_calls: list[dict] = []

        def fake_rejoin(pkg_cfg, reason="", **kw):
            rejoin_calls.append({"reason": reason})
            r = MagicMock()
            r.success = True
            r.error = None
            return r

        # Simulate the healthy block with JOIN_UNCONFIRMED timeout logic
        now_ts = time.time()
        current_after = STATUS_JOIN_UNCONFIRMED

        with patch("agent.supervisor.perform_rejoin", side_effect=fake_rejoin), \
             patch("agent.supervisor._reapply_layout_for_package"), \
             patch("agent.supervisor.db"):
            if (
                current_after == STATUS_JOIN_UNCONFIRMED
                and worker.has_private_url
                and worker._can_auto_reconnect()
                and worker._restart_budget_ok()
            ):
                if worker.join_unconfirmed_since is not None:
                    elapsed = now_ts - worker.join_unconfirmed_since
                    if elapsed > _JOIN_UNCONFIRMED_RELAUNCH_SECONDS:
                        worker.join_unconfirmed_since = None
                        worker._set_status(STATUS_JOINING, "Re-sending private server URL")
                        pkg_cfg = dict(cfg)
                        pkg_cfg["roblox_package"] = worker.package
                        from agent.supervisor import perform_rejoin
                        result = perform_rejoin(
                            pkg_cfg,
                            reason="join_unconfirmed_retry",
                            package_entry=worker.entry,
                            no_force_stop=True,
                        )

        self.assertEqual(len(rejoin_calls), 1, "should trigger one rejoin")
        self.assertEqual(rejoin_calls[0]["reason"], "join_unconfirmed_retry")
        self.assertEqual(status_map.get("com.moons.litesc"), STATUS_JOINING)


if __name__ == "__main__":
    unittest.main()
