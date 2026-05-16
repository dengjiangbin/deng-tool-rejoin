"""Supervisor must reconnect when a Roblox APK / window is closed.

Tests cover:
  * Stale TaskRecord does NOT keep package marked alive forever.
  * Closed package (no process, no drawing window) is detected as dead.
  * perform_rejoin is called during recovery with the private URL.
  * Layout is re-applied (XML rewrite + Set-enable booleans) during recovery.
  * Worker exceptions do NOT kill the supervisor; the loop continues forever.
  * stop_event is the only normal exit path.
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import android
from agent.supervisor import (
    MultiPackageSupervisor,
    _PackageWorker,
    _reapply_layout_for_package,
    STATUS_FAILED,
    STATUS_LAUNCHING,
    STATUS_RECONNECTING,
    STATUS_JOINING,
)


class TestStrictAliveImmuneToStaleTask(unittest.TestCase):
    """Task-only evidence (no process, no drawing window) is NOT alive.

    This is the key fix that lets the supervisor notice when the user closes
    a Roblox clone window in App Cloner.
    """

    def test_only_task_returns_strict_alive_false(self):
        with (
            patch.object(android, "is_process_running", return_value=False),
            patch.object(android, "is_process_running_root", return_value=False),
            patch.object(android, "is_package_task_visible", return_value=True),
            patch.object(android, "is_package_window_visible", return_value=False),
        ):
            ev = android.get_package_alive_evidence("com.roblox.client")
        self.assertTrue(ev["task"])
        self.assertFalse(ev["window"])
        self.assertFalse(ev["running"])
        self.assertFalse(ev["alive"])
        self.assertFalse(ev["strict_alive"])

    def test_window_with_surface_is_alive(self):
        with (
            patch.object(android, "is_process_running", return_value=False),
            patch.object(android, "is_process_running_root", return_value=False),
            patch.object(android, "is_package_task_visible", return_value=False),
            patch.object(android, "is_package_window_visible", return_value=True),
        ):
            ev = android.get_package_alive_evidence("com.roblox.client")
        self.assertTrue(ev["alive"])
        self.assertTrue(ev["strict_alive"])

    def test_process_running_is_alive(self):
        with (
            patch.object(android, "is_process_running", return_value=True),
            patch.object(android, "is_process_running_root", return_value=False),
            patch.object(android, "is_package_task_visible", return_value=False),
            patch.object(android, "is_package_window_visible", return_value=False),
        ):
            ev = android.get_package_alive_evidence("com.roblox.client")
        self.assertTrue(ev["alive"])


class TestWindowVisibleRequiresSurface(unittest.TestCase):
    """is_package_window_visible only returns True for drawing surfaces."""

    def _mock_with(self, dumpsys_output):
        def _mock_run(args, *a, **kw):
            if args[0] == "dumpsys" and args[1] == "window":
                return android.CommandResult(tuple(args), 0, dumpsys_output, "")
            return android.CommandResult(tuple(args), 1, "", "")
        return _mock_run

    def test_stale_window_without_surface_returns_false(self):
        stale = (
            "Window #0 Window{a u0 com.roblox.client/.MainActivity}:\n"
            "  mHasSurface=false\n"
            "  mIsExiting=true\n"
        )
        with patch.object(android, "run_command",
                          side_effect=self._mock_with(stale)):
            self.assertFalse(android.is_package_window_visible("com.roblox.client"))

    def test_window_with_surface_returns_true(self):
        live = (
            "Window #0 Window{a u0 com.roblox.client/.MainActivity}:\n"
            "  mFrame=[100,200][800,500]\n"
            "  mHasSurface=true\n"
        )
        with patch.object(android, "run_command",
                          side_effect=self._mock_with(live)):
            self.assertTrue(android.is_package_window_visible("com.roblox.client"))


class TestReapplyLayoutOnRecovery(unittest.TestCase):
    """_reapply_layout_for_package must call window_apply silently."""

    def test_calls_apply_window_layout_silent(self):
        from agent import window_apply, window_layout
        calls: list = []

        def _fake_silent(rects, **kw):
            calls.append([r.package for r in rects])
            return (1, 1)

        with patch.object(window_apply, "apply_window_layout_silent",
                          side_effect=_fake_silent):
            _reapply_layout_for_package("com.roblox.client")
        self.assertTrue(calls, "apply_window_layout_silent must be called")
        self.assertEqual(calls[0], ["com.roblox.client"])

    def test_never_raises_even_when_apply_throws(self):
        from agent import window_apply
        with patch.object(window_apply, "apply_window_layout_silent",
                          side_effect=RuntimeError("boom")):
            try:
                _reapply_layout_for_package("com.roblox.client")
            except Exception as exc:  # noqa: BLE001
                self.fail(f"_reapply_layout_for_package raised: {exc}")


class TestWorkerSurvivesExceptions(unittest.TestCase):
    """A health-check exception must NOT silently kill the worker thread."""

    def test_worker_logs_and_continues(self):
        stop_event = threading.Event()
        entry = {
            "package": "com.roblox.client",
            "auto_reopen_enabled": True,
            "auto_reconnect_enabled": True,
        }
        cfg = {
            "auto_rejoin_enabled": True,
            "supervisor": {
                "enabled": True,
                "health_check_interval_seconds": 1,
                "launch_grace_seconds": 1,
            },
        }
        status_map = {"com.roblox.client": "Preparing"}
        worker = _PackageWorker(entry, cfg, status_map, stop_event)

        call_count = {"n": 0}

        def _flaky_health(*a, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("first call boom")
            stop_event.set()
            return MagicMock(state="healthy", message="ok", meta={
                "running": True, "foreground": "com.roblox.client",
                "task": True, "window": True, "is_foreground": True,
            })

        with patch("agent.supervisor.check_package_health", side_effect=_flaky_health):
            worker.run()

        # Worker survived first exception and kept looping.
        self.assertGreater(call_count["n"], 1)


class TestReviveCallsRejoinAndLayout(unittest.TestCase):
    """When package is dead, worker must reapply layout + call perform_rejoin."""

    def test_revive_path_invokes_perform_rejoin_and_reapply(self):
        stop_event = threading.Event()
        entry = {
            "package": "com.roblox.client",
            "auto_reopen_enabled": True,
            "auto_reconnect_enabled": True,
            "private_server_url":
                "https://www.roblox.com/games/1/x?privateServerLinkCode=test",
        }
        cfg = {
            "auto_rejoin_enabled": True,
            "supervisor": {
                "enabled": True,
                "health_check_interval_seconds": 1,
                "launch_grace_seconds": 0,
                "max_restart_attempts_per_hour": 100,
                "restart_backoff_seconds": 1,
            },
            "reconnect_delay_seconds": 1,
        }
        status_map = {"com.roblox.client": "Online"}
        worker = _PackageWorker(entry, cfg, status_map, stop_event)

        dead = MagicMock(state="roblox_not_running",
                         message="no proc",
                         meta={"running": False, "foreground": None,
                               "task": False, "window": False,
                               "disconnect_category": None})

        rejoin_calls: list[dict] = []
        layout_calls: list[str] = []

        def _rejoin(cfg, *, reason, package_entry=None, no_force_stop=False, **_):
            rejoin_calls.append({
                "reason": reason,
                "package": (package_entry or {}).get("package"),
                "private_url": (package_entry or {}).get("private_server_url"),
            })
            stop_event.set()
            return MagicMock(success=True, error=None)

        def _layout(pkg):
            layout_calls.append(pkg)

        with (
            patch("agent.supervisor.check_package_health", return_value=dead),
            patch("agent.supervisor.perform_rejoin", side_effect=_rejoin),
            patch("agent.supervisor._reapply_layout_for_package", side_effect=_layout),
        ):
            worker.run()

        self.assertGreaterEqual(len(rejoin_calls), 1,
            "perform_rejoin must be called when package is dead")
        self.assertEqual(rejoin_calls[0]["package"], "com.roblox.client")
        self.assertIn("privateServerLinkCode", str(rejoin_calls[0]["private_url"]))
        self.assertIn("com.roblox.client", layout_calls,
            "_reapply_layout_for_package must be called during recovery")


class TestSupervisorRunsForever(unittest.TestCase):
    """MultiPackageSupervisor.run_forever loops until stop_event is set."""

    def test_render_callback_invoked_until_stop(self):
        entries = [{"package": "com.roblox.client",
                    "account_username": "U", "enabled": True}]
        cfg = {
            "roblox_package": "com.roblox.client",
            "supervisor": {"enabled": False},  # workers idle (set Offline+sleep)
        }
        sup = MultiPackageSupervisor(entries, cfg)
        render_calls = {"n": 0}

        def _render():
            render_calls["n"] += 1
            if render_calls["n"] >= 2:
                sup.stop_event.set()

        sup.run_forever(display_interval=0.05, render_callback=_render)
        self.assertGreaterEqual(render_calls["n"], 2)

    def test_render_callback_exception_does_not_kill_supervisor(self):
        entries = [{"package": "com.roblox.client", "enabled": True}]
        cfg = {"roblox_package": "com.roblox.client",
               "supervisor": {"enabled": False}}
        sup = MultiPackageSupervisor(entries, cfg)
        render_calls = {"n": 0}

        def _flaky_render():
            render_calls["n"] += 1
            if render_calls["n"] == 1:
                raise RuntimeError("boom")
            sup.stop_event.set()

        sup.run_forever(display_interval=0.05, render_callback=_flaky_render)
        self.assertGreaterEqual(render_calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
