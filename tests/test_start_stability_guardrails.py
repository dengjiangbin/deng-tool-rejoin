from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from agent import android, commands, safe_http, safe_io, supervisor


PROJECT = Path(__file__).resolve().parents[1]
DOC = PROJECT / "docs" / "START_STABILITY_GUARDRAILS.md"


class TestStartStabilityGuardrails(unittest.TestCase):
    def test_guardrail_doc_exists_and_names_required_rules(self) -> None:
        text = DOC.read_text(encoding="utf-8")
        for needle in (
            'os.system("clear")',
            "serialized runner",
            "one `WatchdogSupervisor`",
            "Auto Execute",
            "Landscape and Portrait layout state",
            "10 minutes",
            "Ctrl+C",
            "Dead -> relaunch",
        ):
            self.assertIn(needle, text)

    def test_start_has_single_supervisor_and_no_render_thread(self) -> None:
        source = inspect.getsource(commands.cmd_start)
        self.assertEqual(source.count("WatchdogSupervisor("), 1)
        self.assertEqual(source.count("_supervisor.run_forever("), 1)
        self.assertNotIn("threading.Thread", source)

    def test_android_root_runner_exposes_one_global_lock(self) -> None:
        self.assertIs(android.subprocess_lock(), android.subprocess_lock())
        source = inspect.getsource(android.run_command)
        self.assertIn("with _subprocess_lock", source)

    def test_curl_and_android_share_subprocess_serialization(self) -> None:
        source = inspect.getsource(safe_http._run_curl)
        self.assertIn("subprocess_lock", source)
        self.assertIn("with lock", source)

    def test_public_start_supervisor_does_not_reference_auto_execute_module(self) -> None:
        source = inspect.getsource(supervisor)
        self.assertNotIn("from . import auto_execute", source)
        self.assertNotIn("run_auto_execute_for_package", source)

    def test_crash_context_is_written_for_native_failures(self) -> None:
        source = inspect.getsource(safe_io)
        self.assertIn("set_crash_context", source)
        self.assertIn("[DENG_REJOIN_CRASH_CONTEXT]", source)
        self.assertIn("FAULT_HANDLER_LOG_PATH", source)

    def test_start_does_not_use_os_system_clear(self) -> None:
        source = inspect.getsource(commands.cmd_start)
        self.assertNotIn('os.system("clear")', source)
        self.assertNotIn("os.system('clear')", source)


if __name__ == "__main__":
    unittest.main()
