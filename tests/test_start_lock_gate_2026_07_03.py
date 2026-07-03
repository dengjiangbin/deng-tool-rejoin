"""Start license gate + instance lock fixes (2026-07-03)."""

from __future__ import annotations

import inspect
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import commands, keystore, start_lifecycle
from agent.lockfile import LockManager, LockPermissionError, resolve_writable_instance_lock_paths


def _minimal_start_cfg(**overrides):
    cfg = {
        "first_setup_completed": True,
        "roblox_package": "com.moons.litesc",
        "packages": [
            {
                "package": "com.moons.litesc",
                "enabled": True,
                "account_username": "User1",
            }
        ],
        "license": {"enabled": True, "mode": "remote", "key": "DENG-1111-2222-3333-4444"},
        "license_key": "DENG-1111-2222-3333-4444",
        "supervisor": {"enabled": False},
    }
    cfg.update(overrides)
    return cfg


class StartOrderingTests(unittest.TestCase):
    def test_license_and_lock_run_before_prep_and_cache(self) -> None:
        source = inspect.getsource(commands.cmd_start)
        license_gate = source.find("_cmd_start_run_license_gate(")
        lock_gate = source.find("_cmd_start_acquire_instance_lock(")
        imm_prep = source.find("# ── IMMEDIATE PREP")
        imm_cc = source.find("# ── IMMEDIATE CACHE CLEAR")
        self.assertGreater(license_gate, -1)
        self.assertGreater(lock_gate, -1)
        self.assertGreater(imm_prep, -1)
        self.assertGreater(imm_cc, -1)
        self.assertLess(license_gate, lock_gate)
        self.assertLess(lock_gate, imm_prep)
        self.assertLess(imm_prep, imm_cc)
        self.assertNotIn("Could not create Start lock:", source)


class LicenseGateMessageTests(unittest.TestCase):
    def test_expired_license_message_is_clean(self) -> None:
        msg = commands._license_failure_user_message("expired", "raw server text")
        self.assertEqual(msg, "License expired. Please generate a new key.")

    def test_invalid_license_message_is_clean(self) -> None:
        msg = commands._license_failure_user_message("invalid", "raw")
        self.assertEqual(msg, "License invalid. Please enter a valid key.")

    def test_expired_key_does_not_attempt_lock_or_prep(self) -> None:
        cfg = _minimal_start_cfg()
        start_lifecycle.reset_for_start(["com.moons.litesc"])
        buf = io.StringIO()
        with (
            patch.object(keystore, "DEV_MODE", False),
            patch("agent.commands.is_test_license_bypass_active", return_value=False),
            patch("agent.commands._remote_license_run_check", return_value=("expired", "expired")),
            patch("agent.commands._cmd_start_acquire_instance_lock") as mock_lock,
            patch("agent.commands.run_callable_with_deadline") as mock_deadline,
            redirect_stdout(buf),
        ):
            ok = commands._cmd_start_run_license_gate(
                cfg,
                use_color=False,
                start_log=MagicMock(),
                start_lifecycle=start_lifecycle,
            )
        self.assertFalse(ok)
        mock_lock.assert_not_called()
        mock_deadline.assert_not_called()
        snap = start_lifecycle.probe_snapshot()
        self.assertEqual(snap["license_check_result"], "expired")
        self.assertIsNone(snap["start_lock_create_started_at"])
        self.assertIn("License expired", buf.getvalue())
        self.assertNotIn("[Errno", buf.getvalue())


class StartLockTests(unittest.TestCase):
    def test_resolve_prefers_writable_data_runtime_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            run_dir = root / "install" / "run"

            def _probe(directory: Path) -> tuple[bool, str | None, int | None]:
                if directory == run_dir:
                    return False, "PermissionError", 1
                return True, None, None

            with (
                patch("agent.lockfile.RUN_DIR", run_dir),
                patch("agent.lockfile.DATA_DIR", data_dir),
                patch("agent.lockfile._probe_dir_writable", side_effect=_probe),
            ):
                pid_path, lock_path = resolve_writable_instance_lock_paths()
            self.assertIn("runtime", str(lock_path).replace("\\", "/"))
            manager = LockManager(pid_path=pid_path, lock_path=lock_path)
            manager.acquire()
            try:
                self.assertTrue(pid_path.parent.exists())
                self.assertTrue(lock_path.is_file())
            finally:
                manager.release()

    def test_lock_permission_failure_shows_clean_message(self) -> None:
        cfg = _minimal_start_cfg()
        start_lifecycle.reset_for_start(["com.moons.litesc"])
        session = MagicMock()
        buf = io.StringIO()
        err = PermissionError(1, "Operation not permitted")
        with (
            patch("agent.commands.ensure_app_dirs"),
            patch(
                "agent.commands.resolve_writable_instance_lock_paths",
                return_value=(Path("/tmp/agent.pid"), Path("/tmp/agent.lock")),
            ),
            patch.object(LockManager, "acquire", side_effect=LockPermissionError(str(err))),
            patch(
                "agent.commands.lock_acquire_trace",
                return_value={
                    "operation": "write_text",
                    "start_lock_create_errno": 1,
                    "start_lock_create_error_type": "PermissionError",
                },
            ),
            redirect_stdout(buf),
        ):
            lock, rc = commands._cmd_start_acquire_instance_lock(
                session,
                MagicMock(),
                start_lifecycle,
            )
        self.assertIsNone(lock)
        self.assertEqual(rc, 1)
        out = buf.getvalue()
        self.assertIn(commands._START_LOCK_USER_MESSAGE, out)
        self.assertNotIn("[Errno 1]", out)
        self.assertNotIn("Operation not permitted", out)
        snap = start_lifecycle.probe_snapshot()
        self.assertEqual(snap["start_lock_create_result"], "failed")
        self.assertEqual(snap["start_lock_create_errno"], 1)
        self.assertEqual(snap["start_lock_create_error_type"], "PermissionError")
        session.finish.assert_called_with("lock_failed")

    def test_valid_license_creates_lock_then_lifecycle_stamps(self) -> None:
        cfg = _minimal_start_cfg()
        start_lifecycle.reset_for_start(["com.moons.litesc"])
        session = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp) / "runtime"
            pid_path = tmp_path / "agent.pid"
            lock_path = tmp_path / "agent.lock"
            with (
                patch("agent.commands.ensure_app_dirs"),
                patch(
                    "agent.commands.resolve_writable_instance_lock_paths",
                    return_value=(pid_path, lock_path),
                ),
                patch("agent.commands._remote_license_run_check", return_value=("active", "ok")),
                patch.object(keystore, "DEV_MODE", False),
                patch("agent.commands.is_test_license_bypass_active", return_value=False),
            ):
                self.assertTrue(
                    commands._cmd_start_run_license_gate(
                        cfg,
                        use_color=False,
                        start_log=MagicMock(),
                        start_lifecycle=start_lifecycle,
                    )
                )
                lock, rc = commands._cmd_start_acquire_instance_lock(
                    session,
                    MagicMock(),
                    start_lifecycle,
                )
                try:
                    self.assertEqual(rc, 0)
                    self.assertIsNotNone(lock)
                    stamp = start_lifecycle.begin_prepare_immediately()
                finally:
                    if lock is not None:
                        lock.release()
        snap = start_lifecycle.probe_snapshot()
        self.assertEqual(snap["license_check_result"], "active")
        self.assertEqual(snap["start_lock_create_result"], "ok")
        self.assertIsNotNone(snap["start_lock_path"])
        self.assertIsNotNone(snap["lifecycle_command_sent_at"])
        self.assertGreaterEqual(snap["lifecycle_command_sent_at"], stamp - 0.05)


if __name__ == "__main__":
    unittest.main()
