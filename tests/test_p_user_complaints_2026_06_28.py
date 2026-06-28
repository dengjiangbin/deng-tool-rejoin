"""Regression tests for three user-reported CLI/login complaints (2026-06-28):

1. License key check times out and locks out RETURNING active users during a
   license-server / Supabase outage — even though a 30-day offline grace exists.
   Root cause: the grace branch also required ``_license_session_validated`` (a
   successful check earlier in THIS process run), which is always False on a cold
   start, so a user who simply restarted the tool during the outage was dropped
   to the failure menu. Fixed: cold-start offline grace via
   ``_license_should_offline_grace`` (cached active + within window), still never
   for a freshly-typed key.

2. Double prompt after a timeout: ``Choose [1/0]: Choose [1/0]:`` /
   ``Enter license key: Enter license key:``. Root cause: ``_write_prompt`` wrote
   the prompt to BOTH ``sys.stdout`` and ``/dev/tty`` which on Termux are the
   same terminal. Fixed: write once; only mirror to /dev/tty when stdout is not a
   TTY.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest import mock

from agent import safe_io
from agent import commands
from agent.commands import _ensure_remote_license_menu_loop
from agent.config import default_config


def _iso(delta_seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=delta_seconds)).isoformat()


def _args() -> argparse.Namespace:
    return argparse.Namespace(no_color=True)


# ── #2 double-prompt ──────────────────────────────────────────────────────────

class WritePromptNoDoubleEchoTest(unittest.TestCase):
    """_write_prompt must emit the prompt exactly once on a normal TTY."""

    def test_tty_stdout_writes_prompt_once_only(self) -> None:
        buf = io.StringIO()
        buf.isatty = lambda: True  # type: ignore[attr-defined]
        opened: list[str] = []

        def fake_open(*_a, **_k):  # pragma: no cover - should never run here
            opened.append("dev_tty")
            raise AssertionError("/dev/tty must NOT be written when stdout is a TTY")

        with mock.patch.object(sys, "stdout", buf), \
             mock.patch("builtins.open", side_effect=fake_open):
            safe_io._write_prompt("Choose [1/0]: ")

        self.assertEqual(buf.getvalue(), "Choose [1/0]: ")
        self.assertEqual(buf.getvalue().count("Choose [1/0]:"), 1)
        self.assertEqual(opened, [])

    def test_non_tty_stdout_falls_back_to_dev_tty(self) -> None:
        if os.name == "nt":
            self.skipTest("/dev/tty fallback is POSIX-only")
        buf = io.StringIO()
        buf.isatty = lambda: False  # type: ignore[attr-defined]
        tty_writes: list[str] = []

        class _FakeTty:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def write(self, s):
                tty_writes.append(s)

            def flush(self):
                pass

        with mock.patch.object(sys, "stdout", buf), \
             mock.patch("builtins.open", return_value=_FakeTty()):
            safe_io._write_prompt("Enter license key: ")

        # stdout still gets it once; /dev/tty also gets it once (redirected case).
        self.assertEqual(buf.getvalue().count("Enter license key:"), 1)
        self.assertEqual(tty_writes, ["Enter license key: "])


# ── #1 cold-start offline grace ───────────────────────────────────────────────

class ColdStartOfflineGraceTest(unittest.TestCase):
    """A returning active user is NOT locked out by a transient check failure on
    a fresh process start (offline grace must not require an in-process success).
    """

    def setUp(self) -> None:
        # Ensure no leaked in-process validation from other tests.
        commands._license_session_validated = False

    def _run_gate(self, check_result, lic_extra=None, manual_inputs=None):
        cfg_store = default_config()
        lic = cfg_store.setdefault("license", {})
        lic["key"] = "DENG-OLD-KEY-AAAA-BBBB-CCCC"
        cfg_store["license_key"] = "DENG-OLD-KEY-AAAA-BBBB-CCCC"
        if lic_extra:
            lic.update(lic_extra)

        inputs = iter(manual_inputs or [])

        def fake_read(prompt="", **_kw):
            return next(inputs)

        buf = io.StringIO()
        with mock.patch("agent.commands.load_config", side_effect=lambda: deepcopy(cfg_store)), \
             mock.patch("agent.commands.save_config", side_effect=lambda c: c), \
             mock.patch("agent.commands._ensure_install_id_saved", side_effect=lambda c: c), \
             mock.patch("agent.commands._remote_license_run_check", return_value=check_result), \
             mock.patch("agent.commands._remote_license_run_bind", return_value=check_result), \
             mock.patch("agent.commands.validate_license_key", side_effect=lambda k: k.strip()), \
             mock.patch("agent.commands._is_interactive", return_value=True), \
             mock.patch("agent.commands._persist_license_status", side_effect=lambda c, _s: c), \
             mock.patch("agent.commands._license_gate_user_exit", return_value=False), \
             mock.patch("agent.commands.safe_io.read_interactive_line", side_effect=fake_read):
            sys.stdout = buf
            try:
                ok = _ensure_remote_license_menu_loop({}, _args(), False)
            finally:
                sys.stdout = sys.__stdout__
        return ok, buf.getvalue()

    def test_returning_active_user_passes_on_timeout_cold_start(self) -> None:
        ok, _out = self._run_gate(
            ("check_timeout", "timed out"),
            lic_extra={"last_status": "active", "last_check_at": _iso(3600)},
        )
        self.assertTrue(ok, "recently-active user must be granted offline grace on a cold-start timeout")

    def test_server_unavailable_also_grants_grace(self) -> None:
        ok, _out = self._run_gate(
            ("server_unavailable", "License server temporarily unavailable."),
            lic_extra={"last_status": "active", "last_check_at": _iso(120)},
        )
        self.assertTrue(ok)

    def test_no_cached_active_still_shows_menu_on_timeout(self) -> None:
        # New user (no cached active) → grace must NOT apply; they choose 0 (exit).
        ok, out = self._run_gate(
            ("check_timeout", "timed out"),
            lic_extra=None,
            manual_inputs=["0"],
        )
        self.assertFalse(ok)
        self.assertIn("License check timed out", out)

    def test_grace_window_expired_shows_menu(self) -> None:
        ok, out = self._run_gate(
            ("check_timeout", "timed out"),
            lic_extra={"last_status": "active", "last_check_at": _iso(40 * 24 * 3600)},
            manual_inputs=["0"],
        )
        self.assertFalse(ok, "a 40-day-old cache is outside the 30-day grace window")
        self.assertIn("License check timed out", out)


if __name__ == "__main__":
    unittest.main()
