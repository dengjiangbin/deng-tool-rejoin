"""Start/launch-all segfault regression tests for probe p-03b5e2269a."""

from __future__ import annotations

import inspect
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import android, commands, safe_io
from agent.constants import FAULT_HANDLER_LOG_PATH


class StartSegfaultRegressionTests(unittest.TestCase):
    def test_launch_all_does_not_call_os_system_clear(self) -> None:
        source = inspect.getsource(commands.cmd_start)
        self.assertNotIn('os.system("clear")', source)
        self.assertNotIn("os.system('clear')", source)

    def test_safe_clear_screen_uses_ansi_on_termux(self) -> None:
        source = inspect.getsource(safe_io.safe_clear_screen)
        self.assertIn("\\033[2J\\033[H", source)
        self.assertNotIn('os.system("clear")', source)
        self.assertNotIn("os.system('clear')", source)

    def test_android_subprocess_calls_are_serialized(self) -> None:
        source = inspect.getsource(android.run_command)
        self.assertIn("run_isolated_text", source)
        self.assertIn("lock=subprocess_lock()", source)
        self.assertNotIn("lock=_subprocess_lock()", source)

    def test_root_commands_route_through_serialized_runner(self) -> None:
        source = inspect.getsource(android.run_root_command)
        self.assertIn("return run_command", source)

    def test_start_uses_single_watchdog_instance(self) -> None:
        source = inspect.getsource(commands.cmd_start)
        self.assertEqual(source.count("WatchdogSupervisor("), 1)
        self.assertEqual(source.count("_supervisor.run_forever("), 1)

    def test_start_has_no_duplicate_render_loop_thread(self) -> None:
        source = inspect.getsource(commands.cmd_start)
        self.assertNotIn("threading.Thread", source)
        self.assertNotIn("Thread(", source)

    def test_start_batch_cache_clear_deferred_render(self) -> None:
        source = inspect.getsource(commands.cmd_start)
        batch_idx = source.find("batch_clear_cache_begin")
        done_idx = source.find("batch_clear_cache_done", batch_idx)
        block = source[batch_idx:done_idx]
        self.assertIn("_set_all_phase_labels", block)
        self.assertNotIn('_set_all_phase("Clear Cache"', block)

    def test_live_dashboard_caches_package_ram_polling(self) -> None:
        source = inspect.getsource(commands.cmd_start)
        self.assertIn("get_package_ram_usage", source)
        self.assertIn("_usage_cache", source)
        self.assertNotIn('dumpsys", "meminfo"', source)

    def test_render_loop_writes_only_from_start_owner(self) -> None:
        source = inspect.getsource(commands.cmd_start)
        live_dashboard = source[source.index("def _live_dashboard"):]
        self.assertIn("safe_io.write_stdout_block", live_dashboard)
        self.assertNotIn("threading.Thread", live_dashboard)

    def test_start_handles_keyboard_interrupt_and_restores_terminal(self) -> None:
        source = inspect.getsource(commands.cmd_start)
        self.assertIn("except KeyboardInterrupt", source)
        self.assertIn("_clear_terminal()", source)
        self.assertIn("_termux_exit_clean()", source)

    def test_start_top_level_catches_exceptions(self) -> None:
        source = inspect.getsource(commands.cmd_start)
        self.assertIn("except Exception as exc", source)
        self.assertIn("Agent start failed", source)
        self.assertIn("_supervisor_ref.stop", source)

    def test_main_handles_eof_cleanly(self) -> None:
        source = inspect.getsource(commands.main)
        self.assertIn("except EOFError", source)
        self.assertIn("_termux_exit_clean()", source)

    def test_faulthandler_log_path_is_initialized(self) -> None:
        source = inspect.getsource(safe_io.setup_faulthandler)
        self.assertIn("FAULT_HANDLER_LOG_PATH", source)
        self.assertIn("crash_faulthandler.log", str(FAULT_HANDLER_LOG_PATH))
        self.assertIn("faulthandler.enable", source)
        self.assertIn("all_threads=True", source)
        self.assertIn("os.set_inheritable", source)
        self.assertIn("setup_faulthandler._crash_file", source)

    def test_start_records_crash_phase_context(self) -> None:
        source = inspect.getsource(commands.cmd_start)
        self.assertIn("safe_io.set_crash_context", source)
        self.assertIn("session_id", source)
        self.assertIn("screen_mode", source)
        self.assertIn("package_count", source)

    def test_repeated_start_guard_uses_lockfile(self) -> None:
        source = inspect.getsource(commands.cmd_start)
        self.assertIn("LockManager()", source)
        self.assertIn("stop_running_agent", source)
        self.assertIn("_release_start_lock", source)

    def test_run_command_timeout_returns_clean_result(self) -> None:
        with patch(
            "agent.subprocess_isolated.run_isolated_text",
            return_value=(-1, "", "timed out", True),
        ):
            result = android.run_command(["pidof", "com.moons.litesc"], timeout=1)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.returncode, 124)

    def test_safe_http_curl_uses_same_subprocess_lock(self) -> None:
        from agent import safe_http

        source = inspect.getsource(safe_http._run_curl)
        self.assertIn("run_isolated_bytes", source)
        self.assertIn("lock=_subprocess_lock()", source)
        iso_source = inspect.getsource(__import__("agent.subprocess_isolated", fromlist=["x"]))
        self.assertIn("with lock:", iso_source)

    def test_no_termux_os_system_clear_anywhere_in_agent_start_modules(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (PROJECT / "agent").glob("*.py")
            if path.name in {"commands.py", "supervisor.py", "android.py", "safe_io.py"}
        )
        self.assertNotIn('os.system("clear")', combined)
        self.assertNotIn("os.system('clear')", combined)
        if os.name != "nt":
            self.assertNotIn("os.system(\"cls\")", combined)


if __name__ == "__main__":
    unittest.main()
