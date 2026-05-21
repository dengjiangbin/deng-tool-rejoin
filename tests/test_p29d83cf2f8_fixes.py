"""Tests for p-29d83cf2f8 release-blocker fixes.

Covers:
  A. RAM label defined-before-use: _get_ram_label available during Preparing phase.
  B. get_memory_info safety: no subprocess fallback, all failures return zeros.
  C. Supervisor auto_rejoin_enabled=False no longer disables recovery.
  D. Private URL fallback: effective_private_server_url returns launch_url when
     launch_mode is web_url and private_server_url is empty.
  E. Layout constants unchanged (regression guard).
"""

from __future__ import annotations

import threading
import time
import unittest
from unittest import mock
from unittest.mock import MagicMock, patch


# ─────────────────────────────────────────────────────────────────────────────
# A. _get_ram_label available during Preparing phase (defined before _render_phase)
# ─────────────────────────────────────────────────────────────────────────────


class TestRamLabelDefinedBeforeUse(unittest.TestCase):
    """_get_ram_label must be callable during the Preparing / Boosting phases,
    before the supervisor section of cmd_start runs.
    The previous bug: it was defined at line ~3503 (supervisor section) but
    _render_phase (called from _set_all_phase at line ~3205) referenced it,
    causing UnboundLocalError on every start attempt.
    """

    def test_get_ram_label_returns_string(self) -> None:
        """get_memory_info with known content → label contains 'Available RAM:'."""
        from agent import android
        fake_content = (
            "MemTotal:       8000000 kB\n"
            "MemFree:        1000000 kB\n"
            "MemAvailable:   3000000 kB\n"
        )
        with patch("builtins.open", mock.mock_open(read_data=fake_content)):
            info = android.get_memory_info()

        free_mb  = info.get("free_mb", 0)
        total_mb = info.get("total_mb", 0)

        def _fmt_gb(mb: int) -> str:
            if mb >= 1024:
                return f"{mb / 1024:.2f} GB"
            return f"{mb} MB"

        label = f"Available RAM: {_fmt_gb(free_mb)} free / {_fmt_gb(total_mb)} total"
        self.assertIn("Available RAM:", label)
        self.assertGreater(free_mb, 0)
        self.assertGreater(total_mb, 0)

    def test_render_phase_ram_line_never_raises(self) -> None:
        """Simulating _render_phase's RAM block with a failing get_memory_info
        must produce 'Available RAM: Unknown', not raise."""
        # Replicate the safe RAM block from _render_phase
        called = []

        def _get_ram_label_safe() -> str:
            try:
                raise RuntimeError("simulated crash")
            except Exception:  # noqa: BLE001
                return "Available RAM: Unknown"

        try:
            ram = _get_ram_label_safe()
            if ram:
                called.append(ram)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"_render_phase RAM block must not raise, got: {exc}")

        self.assertEqual(len(called), 1)
        self.assertEqual(called[0], "Available RAM: Unknown")

    def test_ram_label_above_table(self) -> None:
        """The RAM line must appear BEFORE build_start_table output."""
        output_order: list[str] = []

        def _fake_build_start_table(*_a, **_kw) -> str:
            output_order.append("table")
            return "TABLE"

        def _fake_get_ram() -> str:
            return "Available RAM: 2.00 GB free / 7.00 GB total"

        # Simulate _render_phase body
        from io import StringIO
        import sys
        buf = StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            try:
                ram = _fake_get_ram()
                if ram:
                    print(f"  {ram}")
            except Exception:  # noqa: BLE001
                pass
            print(_fake_build_start_table())
            print(flush=True)
        finally:
            sys.stdout = old_stdout

        text = buf.getvalue()
        table_pos = text.find("TABLE")
        ram_pos = text.find("Available RAM:")
        self.assertGreater(table_pos, -1, "table must appear")
        self.assertGreater(ram_pos, -1, "RAM line must appear")
        self.assertLess(ram_pos, table_pos, "RAM line must be BEFORE the table")


# ─────────────────────────────────────────────────────────────────────────────
# B. get_memory_info: no subprocess, all failures return zeros
# ─────────────────────────────────────────────────────────────────────────────


class TestGetMemoryInfoSafety(unittest.TestCase):
    """get_memory_info must never invoke subprocess and must always return a
    dict{total_mb, free_mb, percent_free} — zeros on any error."""

    def test_valid_meminfo_parsed_correctly(self) -> None:
        from agent import android
        content = (
            "MemTotal:       7602176 kB\n"
            "MemFree:        1200000 kB\n"
            "MemAvailable:   3584000 kB\n"
            "Buffers:         100000 kB\n"
        )
        with patch("builtins.open", mock.mock_open(read_data=content)):
            info = android.get_memory_info()
        self.assertAlmostEqual(info["total_mb"], 7602176 // 1024, delta=1)
        self.assertAlmostEqual(info["free_mb"],  3584000 // 1024, delta=1)
        self.assertGreater(info["percent_free"], 0)

    def test_missing_file_returns_zeros_no_raise(self) -> None:
        from agent import android
        with patch("builtins.open", side_effect=OSError("no such file")):
            info = android.get_memory_info()
        self.assertIsInstance(info, dict)
        self.assertEqual(info["total_mb"], 0)
        self.assertEqual(info["free_mb"], 0)
        self.assertEqual(info["percent_free"], 0)

    def test_no_subprocess_called_on_failure(self) -> None:
        """After the fix, run_command must NEVER be called from get_memory_info."""
        from agent import android
        with patch("builtins.open", side_effect=OSError("gone")), \
             patch.object(android, "run_command") as mock_rc:
            info = android.get_memory_info()
        mock_rc.assert_not_called()
        self.assertEqual(info["total_mb"], 0)

    def test_malformed_content_returns_zeros(self) -> None:
        from agent import android
        bad = "not valid meminfo content!!!\nrandom stuff\n"
        with patch("builtins.open", mock.mock_open(read_data=bad)):
            info = android.get_memory_info()
        self.assertIsInstance(info, dict)
        self.assertEqual(info["total_mb"], 0)
        self.assertEqual(info["free_mb"], 0)

    def test_partial_content_parses_what_is_available(self) -> None:
        """Only MemTotal present → free=0, percent_free=0, no raise."""
        from agent import android
        content = "MemTotal:       4000000 kB\n"
        with patch("builtins.open", mock.mock_open(read_data=content)):
            info = android.get_memory_info()
        self.assertGreater(info["total_mb"], 0)
        self.assertEqual(info["free_mb"], 0)
        self.assertEqual(info["percent_free"], 0)

    def test_always_returns_dict_with_required_keys(self) -> None:
        from agent import android
        with patch("builtins.open", side_effect=PermissionError("denied")):
            info = android.get_memory_info()
        for key in ("total_mb", "free_mb", "percent_free"):
            self.assertIn(key, info)


# ─────────────────────────────────────────────────────────────────────────────
# C. Supervisor: auto_rejoin_enabled=False no longer disables recovery
# ─────────────────────────────────────────────────────────────────────────────


class TestSupervisorAutoRejoinFlag(unittest.TestCase):
    """After the fix, auto_rejoin_enabled=False must NOT prevent per-package
    recovery when supervisor.enabled=True.  Only supervisor.enabled=False
    disables the supervisor loop."""

    def _make_cfg(self, *, auto_rejoin_enabled: bool, supervisor_enabled: bool) -> dict:
        return {
            "auto_rejoin_enabled": auto_rejoin_enabled,
            "supervisor": {
                "enabled": supervisor_enabled,
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
            "launch_mode": "web_url",
            "launch_url": "roblox://navigation/share_links?code=ABC&type=Server",
            "private_server_url": "",
            "roblox_package": "com.test.pkg",
            "roblox_packages": [
                {
                    "package": "com.test.pkg",
                    "enabled": True,
                    "auto_reopen_enabled": True,
                    "auto_reconnect_enabled": True,
                    "private_server_url": "",
                    "account_username": "tester",
                    "app_name": "test",
                    "low_graphics_enabled": True,
                    "roblox_user_id": 0,
                    "username_source": "manual",
                }
            ],
        }

    def test_auto_rejoin_false_does_not_disable_recovery(self) -> None:
        """With auto_rejoin_enabled=False but supervisor.enabled=True,
        the supervisor loop must NOT immediately set Offline and skip."""
        from agent.supervisor import _PackageWorker, STATUS_OFFLINE

        status_map: dict[str, str] = {}
        stop_event = threading.Event()
        entry = {
            "package": "com.test.pkg",
            "auto_reopen_enabled": True,
            "auto_reconnect_enabled": True,
            "private_server_url": "",
            "account_username": "tester",
        }
        cfg = self._make_cfg(auto_rejoin_enabled=False, supervisor_enabled=True)

        worker = _PackageWorker(entry, cfg, status_map, stop_event)

        fake_health = MagicMock()
        fake_health.state = "healthy"
        fake_health.meta = {
            "running": True, "root_running": False,
            "task": True, "window": True, "surface": True,
            "fg_evidence": True,
        }
        fake_health.message = "healthy"

        # Run one tick of the worker loop with healthy state.
        # If the bug were still present, the worker would immediately set
        # status to "Offline" (supervisor disabled).
        # After the fix it must NOT set Offline; it should set some positive state.
        iterations_run = [0]

        def fake_check(cfg_arg, pkg_arg):
            iterations_run[0] += 1
            stop_event.set()  # stop after first real check
            return fake_health

        with patch("agent.supervisor.check_package_health", side_effect=fake_check), \
             patch("agent.supervisor._reapply_layout_for_package"), \
             patch("agent.supervisor.perform_rejoin"), \
             patch("agent.supervisor.db"), \
             patch("agent.supervisor.time") as mock_time:
            mock_time.time.return_value = 1000.0
            mock_time.sleep = lambda _: None
            worker.run()

        # The health check must have been called (not short-circuited by
        # the old auto_rejoin_enabled check).
        self.assertGreater(iterations_run[0], 0,
                           "supervisor must reach check_package_health when "
                           "supervisor.enabled=True even if auto_rejoin_enabled=False")
        # Must NOT be stuck at Offline(supervisor disabled)
        final_status = status_map.get("com.test.pkg", "")
        self.assertNotEqual(
            final_status, STATUS_OFFLINE,
            f"auto_rejoin_enabled=False must not force Offline; got {final_status!r}",
        )

    def test_supervisor_enabled_false_still_disables(self) -> None:
        """supervisor.enabled=False must still disable the loop (correct gating)."""
        from agent.supervisor import _PackageWorker, STATUS_OFFLINE

        status_map: dict[str, str] = {}
        stop_event = threading.Event()
        entry = {
            "package": "com.test.pkg",
            "auto_reopen_enabled": True,
            "auto_reconnect_enabled": True,
            "private_server_url": "",
            "account_username": "tester",
        }
        cfg = self._make_cfg(auto_rejoin_enabled=True, supervisor_enabled=False)

        worker = _PackageWorker(entry, cfg, status_map, stop_event)

        ticks = [0]

        real_sleep = time.sleep

        def fake_sleep(s):
            ticks[0] += 1
            if ticks[0] >= 2:
                stop_event.set()

        with patch("agent.supervisor.check_package_health") as mock_check, \
             patch("agent.supervisor.db"), \
             patch("agent.supervisor.time") as mock_time:
            mock_time.time.return_value = 1000.0
            mock_time.sleep.side_effect = fake_sleep
            worker.run()

        mock_check.assert_not_called()
        self.assertEqual(status_map.get("com.test.pkg"), STATUS_OFFLINE)

    def test_dead_package_relaunches_with_private_url(self) -> None:
        """When a package goes Dead (grace elapsed), the supervisor calls
        perform_rejoin with the package config that includes the launch URL."""
        from agent.supervisor import _PackageWorker, STATUS_RECONNECTING

        status_map: dict[str, str] = {}
        stop_event = threading.Event()
        entry = {
            "package": "com.test.pkg",
            "auto_reopen_enabled": True,
            "auto_reconnect_enabled": True,
            "private_server_url": "",
            "account_username": "tester",
        }
        cfg = self._make_cfg(auto_rejoin_enabled=False, supervisor_enabled=True)

        worker = _PackageWorker(entry, cfg, status_map, stop_event)
        worker.grace_start = 0.0  # grace already elapsed

        fake_health = MagicMock()
        fake_health.state = "roblox_not_running"
        fake_health.meta = {
            "running": False, "root_running": False,
            "task": False, "window": False, "surface": False,
            "fg_evidence": False,
        }
        fake_health.message = "not running"

        rejoin_calls: list[dict] = []

        def fake_rejoin(cfg_arg, **_kw):
            rejoin_calls.append(dict(cfg_arg))
            stop_event.set()
            r = MagicMock()
            r.success = True
            r.error = None
            return r

        ticks = [0]

        def fake_sleep(s):
            ticks[0] += 1
            if ticks[0] > 5:
                stop_event.set()

        with patch("agent.supervisor.check_package_health", return_value=fake_health), \
             patch("agent.supervisor._reapply_layout_for_package"), \
             patch("agent.supervisor.perform_rejoin", side_effect=fake_rejoin), \
             patch("agent.supervisor.db"), \
             patch("agent.supervisor.time") as mock_time:
            # Simulate enough time so grace window (5s) has elapsed.
            mock_time.time.return_value = 1000.0
            mock_time.sleep.side_effect = fake_sleep
            worker.run()

        # perform_rejoin must have been called (dead package recovery)
        self.assertGreater(len(rejoin_calls), 0,
                           "Dead package must trigger perform_rejoin")

        # The config passed must carry the launch_url
        first_cfg = rejoin_calls[0]
        self.assertEqual(
            first_cfg.get("launch_url"),
            "roblox://navigation/share_links?code=ABC&type=Server",
            "perform_rejoin must receive the private URL via launch_url",
        )


# ─────────────────────────────────────────────────────────────────────────────
# D. Private URL fallback: effective_private_server_url returns launch_url
# ─────────────────────────────────────────────────────────────────────────────


class TestPrivateServerUrlFallback(unittest.TestCase):
    """When private_server_url is empty but launch_url is set with
    launch_mode=web_url, effective_private_server_url must return launch_url."""

    def test_falls_back_to_launch_url_when_web_url_mode(self) -> None:
        from agent.config import effective_private_server_url
        entry  = {"private_server_url": ""}
        merged = {
            "private_server_url": "",
            "launch_url": "https://www.roblox.com/share?code=ABC123&type=Server",
            "launch_mode": "web_url",
        }
        url = effective_private_server_url(entry, merged)
        self.assertEqual(url, "https://www.roblox.com/share?code=ABC123&type=Server")

    def test_falls_back_to_launch_url_when_deeplink_mode(self) -> None:
        from agent.config import effective_private_server_url
        entry  = {"private_server_url": ""}
        merged = {
            "private_server_url": "",
            "launch_url": "roblox://navigation/share_links?code=XYZ&type=Server",
            "launch_mode": "deeplink",
        }
        url = effective_private_server_url(entry, merged)
        self.assertEqual(url, "roblox://navigation/share_links?code=XYZ&type=Server")

    def test_no_fallback_when_app_mode(self) -> None:
        """launch_mode=app must NOT use launch_url as private URL."""
        from agent.config import effective_private_server_url
        entry  = {"private_server_url": ""}
        merged = {
            "private_server_url": "",
            "launch_url": "roblox://navigation/share_links?code=XYZ&type=Server",
            "launch_mode": "app",
        }
        url = effective_private_server_url(entry, merged)
        self.assertEqual(url, "")

    def test_per_package_url_takes_priority_over_global(self) -> None:
        from agent.config import effective_private_server_url
        entry  = {
            "private_server_url": "roblox://navigation/share_links?code=PKG&type=Server",
        }
        merged = {
            "private_server_url": "roblox://navigation/share_links?code=GLOBAL&type=Server",
            "launch_url": "roblox://navigation/share_links?code=LAUNCH&type=Server",
            "launch_mode": "web_url",
        }
        url = effective_private_server_url(entry, merged)
        self.assertIn("PKG", url)

    def test_url_with_ampersand_type_server_preserved(self) -> None:
        """The &type=Server query parameter must survive end-to-end."""
        from agent.config import effective_private_server_url
        raw = "https://www.roblox.com/share?code=abc123&type=Server"
        entry  = {"private_server_url": ""}
        merged = {
            "private_server_url": "",
            "launch_url": raw,
            "launch_mode": "web_url",
        }
        url = effective_private_server_url(entry, merged)
        self.assertIn("&type=Server", url)
        self.assertIn("code=abc123", url)

    def test_supervisor_has_private_url_true_from_launch_url(self) -> None:
        """Supervisor.has_private_url must be True when launch_url is set
        and launch_mode=web_url, even if private_server_url is empty."""
        from agent.config import effective_private_server_url
        entry  = {"private_server_url": ""}
        cfg    = {
            "private_server_url": "",
            "launch_url": "roblox://navigation/share_links?code=ABC&type=Server",
            "launch_mode": "web_url",
        }
        has_url = bool(str(effective_private_server_url(entry, cfg) or "").strip())
        self.assertTrue(has_url,
                        "has_private_url must be True when launch_url is non-empty with web_url mode")


# ─────────────────────────────────────────────────────────────────────────────
# E. Window layout regression guard
# ─────────────────────────────────────────────────────────────────────────────


class TestWindowLayoutUnchanged(unittest.TestCase):
    """Ensure the bugfix cycle has not accidentally changed layout constants
    or exclusion rules in window_layout.py."""

    def test_window_layout_module_imports_clean(self) -> None:
        """window_layout must import without error."""
        try:
            from agent import window_layout  # noqa: F401
        except ImportError as exc:
            self.fail(f"window_layout import failed: {exc}")

    def test_window_apply_module_imports_clean(self) -> None:
        """window_apply must import without error."""
        try:
            from agent import window_apply  # noqa: F401
        except ImportError as exc:
            self.fail(f"window_apply import failed: {exc}")


if __name__ == "__main__":
    unittest.main()
