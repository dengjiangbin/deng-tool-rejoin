"""License gate must block on real TTY input — no silent EOF-to-exit."""

from __future__ import annotations

import argparse
import io
import os
import sys
import unittest
from copy import deepcopy
from io import StringIO
from unittest import mock

from agent import safe_io
from agent.commands import _ensure_remote_license_menu_loop, _handle_license_gate_failure
from agent.config import default_config


def _args() -> argparse.Namespace:
    return argparse.Namespace(no_color=True)


class ReadInteractiveLineTests(unittest.TestCase):
    def test_termux_stdin_eof_falls_back_to_dev_tty(self) -> None:
        with mock.patch.dict(os.environ, {"TERMUX_VERSION": "0.118.0"}, clear=False), \
             mock.patch.object(sys.stdin, "readline", return_value=""), \
             mock.patch("agent.safe_io._read_line_from_dev_tty", return_value="1\n"):
            result = safe_io.read_interactive_line("Choose [1/0]: ")
        self.assertEqual(result, "1")

    def test_termux_eof_without_tty_raises_unavailable(self) -> None:
        with mock.patch.dict(os.environ, {"TERMUX_VERSION": "0.118.0"}, clear=False), \
             mock.patch.object(sys.stdin, "readline", return_value=""), \
             mock.patch("agent.safe_io._read_line_from_dev_tty", return_value=None):
            with self.assertRaises(safe_io.InteractiveInputUnavailable):
                safe_io.read_interactive_line("Choose [1/0]: ")

    def test_non_termux_delegates_to_safe_prompt(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TERMUX_VERSION", None)
            with mock.patch("agent.safe_io.safe_prompt", return_value="0") as sp:
                result = safe_io.read_interactive_line("Choose [1/0]: ", default="0")
        self.assertEqual(result, "0")
        sp.assert_called_once()


class LicenseGateBlockingTests(unittest.TestCase):
    def _run_loop(self, inputs: list[str], results: list[tuple[str, str]]) -> tuple[bool, str, list[str]]:
        cfg_store = default_config()
        cfg_store.setdefault("license", {})["key"] = "DENG-OLD-KEY-AAAA-BBBB-CCCC"
        cfg_store["license_key"] = "DENG-OLD-KEY-AAAA-BBBB-CCCC"
        call_idx = {"n": 0}
        verify_keys: list[str] = []

        def mock_check(cfg):
            from agent.commands import _load_license_key_from_cfg
            verify_keys.append(_load_license_key_from_cfg(cfg))
            i = call_idx["n"]
            call_idx["n"] += 1
            return results[i] if i < len(results) else ("active", "ok")

        input_iter = iter(inputs)

        def fake_read(prompt="", **_kwargs):
            return next(input_iter)

        buf = io.StringIO()
        with mock.patch("agent.commands.load_config", side_effect=lambda: deepcopy(cfg_store)), \
             mock.patch("agent.commands.save_config", side_effect=lambda c: c), \
             mock.patch("agent.commands._ensure_install_id_saved", side_effect=lambda c: c), \
             mock.patch("agent.commands._remote_license_run_check", side_effect=mock_check), \
             mock.patch("agent.commands._remote_license_run_bind", side_effect=mock_check), \
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
        return ok, buf.getvalue(), verify_keys

    def test_wrong_device_choice_one_prompts_new_key(self) -> None:
        ok, _out, keys = self._run_loop(
            ["1", "DENG-NEW-KEY-1111-2222-3333"],
            [("wrong_device", "w"), ("active", "ok")],
        )
        self.assertTrue(ok)
        self.assertEqual(keys[0], "DENG-OLD-KEY-AAAA-BBBB-CCCC")
        self.assertEqual(keys[1], "DENG-NEW-KEY-1111-2222-3333")

    def test_timeout_choice_one_prompts_new_key(self) -> None:
        ok, out, keys = self._run_loop(
            ["1", "DENG-NEW-KEY-1111-2222-3333"],
            [("check_timeout", "timed out"), ("active", "ok")],
        )
        self.assertTrue(ok)
        self.assertIn("License check timed out", out)
        self.assertEqual(keys[1], "DENG-NEW-KEY-1111-2222-3333")

    def test_eof_does_not_silently_exit_without_tty(self) -> None:
        cfg = default_config()
        buf = io.StringIO()
        with mock.patch("agent.commands._clear_cached_license_key", side_effect=lambda c: c), \
             mock.patch("agent.commands._print_license_err"), \
             mock.patch("agent.commands._print_license_gate_retry_menu"), \
             mock.patch("agent.commands._prompt_license_gate_choice", side_effect=safe_io.InteractiveInputUnavailable("x")), \
             mock.patch("agent.commands._print_license_gate_input_unavailable") as mock_unavail:
            sys.stdout = buf
            try:
                _cfg, action = _handle_license_gate_failure(cfg, "wrong_device", "w", False)
            finally:
                sys.stdout = sys.__stdout__
        self.assertEqual(action, "unavailable")
        mock_unavail.assert_called_once()

    def test_menu_shows_numbered_options(self) -> None:
        _ok, out, _keys = self._run_loop(["0"], [("invalid", "bad")])
        self.assertIn("1. Enter Different Key", out)
        self.assertIn("0. Exit", out)


if __name__ == "__main__":
    unittest.main()
