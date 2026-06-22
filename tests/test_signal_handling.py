"""Regression: POSIX signal traps restore TTY state and erase runtime locks."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from agent import signal_handler


class TeardownPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        signal_handler._teardown_done = False
        signal_handler._handlers_installed = False
        signal_handler._extra_runtime_paths.clear()

    def test_teardown_erases_agent_lock_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            run_dir = home / "run"
            run_dir.mkdir(parents=True)
            pid_path = run_dir / "agent.pid"
            lock_path = run_dir / "agent.lock"
            pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
            lock_path.write_text(
                json.dumps({"pid": os.getpid(), "product": "DENG Tool: Rejoin"}) + "\n",
                encoding="utf-8",
            )

            with mock.patch("agent.signal_handler.PID_PATH", pid_path), \
                 mock.patch("agent.signal_handler.LOCK_PATH", lock_path), \
                 mock.patch("agent.signal_handler.MONITOR_PID_PATH", run_dir / "monitor-bridge.pid"), \
                 mock.patch("agent.signal_handler.MONITOR_LOCK_PATH", run_dir / "monitor-bridge.lock"), \
                 mock.patch("agent.signal_handler.MONITOR_STATUS_PATH", run_dir / "monitor-bridge.status.json"), \
                 mock.patch("agent.signal_handler.safe_io.restore_terminal") as restore:
                code = signal_handler.run_teardown_pipeline(signal.SIGTERM, exit_process=False)

            self.assertEqual(code, 128 + signal.SIGTERM)
            self.assertFalse(pid_path.exists())
            self.assertFalse(lock_path.exists())
            restore.assert_called_once()

    def test_teardown_skips_foreign_pid_locks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "run"
            run_dir.mkdir(parents=True)
            pid_path = run_dir / "agent.pid"
            pid_path.write_text("999999\n", encoding="utf-8")

            with mock.patch("agent.signal_handler.PID_PATH", pid_path), \
                 mock.patch("agent.signal_handler.LOCK_PATH", run_dir / "agent.lock"), \
                 mock.patch("agent.signal_handler.MONITOR_PID_PATH", run_dir / "m.pid"), \
                 mock.patch("agent.signal_handler.MONITOR_LOCK_PATH", run_dir / "m.lock"), \
                 mock.patch("agent.signal_handler.MONITOR_STATUS_PATH", run_dir / "m.json"), \
                 mock.patch("agent.signal_handler.safe_io.restore_terminal"):
                signal_handler.run_teardown_pipeline(signal.SIGINT, exit_process=False)

            self.assertTrue(pid_path.exists())

    def test_exit_code_follows_128_plus_signum(self) -> None:
        sig = getattr(signal, "SIGHUP", signal.SIGTERM)
        self.assertEqual(
            signal_handler.run_teardown_pipeline(sig, exit_process=False),
            128 + sig,
        )

    def test_second_teardown_hard_exits(self) -> None:
        signal_handler._teardown_done = True
        with mock.patch("agent.signal_handler.os._exit") as hard_exit:
            code = signal_handler.run_teardown_pipeline(signal.SIGTERM, exit_process=False)
        hard_exit.assert_called_once_with(128 + signal.SIGTERM)
        self.assertEqual(code, 128 + signal.SIGTERM)

    def test_install_registers_handlers(self) -> None:
        previous_int = signal.getsignal(signal.SIGINT)
        try:
            signal_handler.install_signal_handlers(force=True)
            self.assertIs(signal.getsignal(signal.SIGINT), signal_handler._handle_signal)
            self.assertIs(signal.getsignal(signal.SIGTERM), signal_handler._handle_signal)
        finally:
            signal.signal(signal.SIGINT, previous_int)
            signal.signal(signal.SIGTERM, signal.getsignal(signal.SIGTERM))


@unittest.skipIf(os.name == "nt", "POSIX os.kill integration requires Unix")
class SignalKillIntegrationTests(unittest.TestCase):
    def test_sigterm_clears_locks_and_exits_with_standard_code(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        child = textwrap.dedent(
            """
            import os, sys, time
            from pathlib import Path

            test_home = Path(sys.argv[1])
            repo_root = Path(sys.argv[2])
            sys.path.insert(0, str(repo_root))
            os.environ["DENG_REJOIN_HOME"] = str(test_home)

            run_dir = test_home / "run"
            run_dir.mkdir(parents=True, exist_ok=True)
            pid_path = run_dir / "agent.pid"
            lock_path = run_dir / "agent.lock"
            pid = os.getpid()
            pid_path.write_text(f"{pid}\\n", encoding="utf-8")
            lock_path.write_text('{"pid": %d}\\n' % pid, encoding="utf-8")

            from agent import signal_handler
            signal_handler.install_signal_handlers(force=True)
            sys.stdout.write("ready\\n")
            sys.stdout.flush()
            while True:
                time.sleep(1)
            """
        )

        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / "sig_child.py"
            script.write_text(child, encoding="utf-8")
            proc = subprocess.Popen(
                [sys.executable, str(script), td, str(repo_root)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                close_fds=True,
            )
            try:
                deadline = time.monotonic() + 5.0
                ready = False
                while time.monotonic() < deadline:
                    line = proc.stdout.readline() if proc.stdout else ""
                    if "ready" in line:
                        ready = True
                        break
                self.assertTrue(ready, "child did not reach ready state")
                os.kill(proc.pid, signal.SIGTERM)
                rc = proc.wait(timeout=5)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=2)

            self.assertEqual(rc, 128 + signal.SIGTERM)
            run_dir = Path(td) / "run"
            self.assertFalse((run_dir / "agent.pid").exists())
            self.assertFalse((run_dir / "agent.lock").exists())


if __name__ == "__main__":
    unittest.main()
