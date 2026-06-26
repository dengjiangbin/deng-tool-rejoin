"""License gate retry UX: unified failure menu, fresh key prompt, clean exit."""

from __future__ import annotations

import argparse
import io
import sys
import unittest
from copy import deepcopy
from unittest.mock import patch

from agent.commands import (
    _clear_cached_license_key,
    _ensure_remote_license_menu_loop,
    _load_license_key_from_cfg,
    _normalize_license_check_result,
)
from agent.config import default_config
from agent.license import LICENSE_CHECK_TIMEOUT_USER_MESSAGE, WRONG_DEVICE_USER_MESSAGE


def _args() -> argparse.Namespace:
    return argparse.Namespace(no_color=True)


class LicenseGateRetryFlowTests(unittest.TestCase):
    def _make_cfg(self, key: str = "DENG-OLD-KEY-AAAA-BBBB-CCCC") -> dict:
        cfg = default_config()
        cfg.setdefault("license", {})["key"] = key
        cfg["license_key"] = key
        return cfg

    def _run_remote_loop(
        self,
        *,
        inputs: list[str],
        remote_results: list[tuple[str, str]],
        initial_key: str = "DENG-OLD-KEY-AAAA-BBBB-CCCC",
    ) -> tuple[bool, str, list[str]]:
        cfg_store = self._make_cfg(initial_key)
        call_idx = {"n": 0}
        verify_keys: list[str] = []

        def mock_check(cfg):
            verify_keys.append(_load_license_key_from_cfg(cfg))
            i = call_idx["n"]
            call_idx["n"] += 1
            if i < len(remote_results):
                return remote_results[i]
            return ("active", "ok")

        input_iter = iter(inputs)

        def fake_prompt(prompt="", default=None, **_kwargs):
            try:
                return next(input_iter)
            except StopIteration:
                return None

        def load_side_effect():
            return deepcopy(cfg_store)

        def save_side_effect(new_cfg):
            cfg_store.clear()
            cfg_store.update(deepcopy(new_cfg))
            return new_cfg

        buf = io.StringIO()
        with patch("agent.commands.load_config", side_effect=load_side_effect), \
             patch("agent.commands.save_config", side_effect=save_side_effect), \
             patch("agent.commands._ensure_install_id_saved", side_effect=lambda c: c), \
             patch("agent.commands._remote_license_run_check", side_effect=mock_check), \
             patch("agent.commands._remote_license_run_bind", side_effect=mock_check), \
             patch("agent.commands.validate_license_key", side_effect=lambda k: k.strip()), \
             patch("agent.commands._is_interactive", return_value=True), \
             patch("agent.commands._persist_license_status", side_effect=lambda c, _s: c), \
             patch("agent.commands._license_gate_user_exit", return_value=False), \
             patch("agent.commands.safe_io.safe_prompt", side_effect=fake_prompt):
            sys.stdout = buf
            try:
                ok = _ensure_remote_license_menu_loop({}, _args(), False)
            finally:
                sys.stdout = sys.__stdout__
        return ok, buf.getvalue(), verify_keys

    def test_unified_menu_numbered_for_all_failures(self):
        for result, msg in [
            ("wrong_device", "w"),
            ("invalid", "bad"),
            ("expired", "expired"),
            ("check_timeout", LICENSE_CHECK_TIMEOUT_USER_MESSAGE),
            ("server_unavailable", "api down"),
        ]:
            with self.subTest(result=result):
                _ok, out, _keys = self._run_remote_loop(
                    inputs=["0"],
                    remote_results=[(result, msg)],
                )
                self.assertIn("1. Enter Different Key", out)
                self.assertIn("0. Exit", out)
                self.assertNotIn("Try Another Key", out)

    def test_wrong_device_message_no_reset_hwid(self):
        _ok, out, _keys = self._run_remote_loop(
            inputs=["0"],
            remote_results=[("wrong_device", "bound elsewhere")],
        )
        self.assertIn("This key is already bound to another device", out)
        self.assertNotIn("Reset HWID", out)

    def test_wrong_device_retry_prompts_new_key(self):
        ok, out, keys = self._run_remote_loop(
            inputs=["1", "DENG-NEW-KEY-1111-2222-3333"],
            remote_results=[("wrong_device", "w"), ("active", "ok")],
        )
        self.assertTrue(ok)
        self.assertEqual(keys[0], "DENG-OLD-KEY-AAAA-BBBB-CCCC")
        self.assertEqual(keys[1], "DENG-NEW-KEY-1111-2222-3333")
        self.assertNotIn("DENG-OLD-KEY-AAAA-BBBB-CCCC", keys[1:])

    def test_invalid_key_retry_prompts_new_key(self):
        ok, _out, keys = self._run_remote_loop(
            inputs=["1", "DENG-NEW-KEY-1111-2222-3333"],
            remote_results=[("invalid", "bad"), ("active", "ok")],
        )
        self.assertTrue(ok)
        self.assertEqual(keys[0], "DENG-OLD-KEY-AAAA-BBBB-CCCC")
        self.assertEqual(keys[1], "DENG-NEW-KEY-1111-2222-3333")

    def test_timeout_retry_prompts_new_key(self):
        ok, out, keys = self._run_remote_loop(
            inputs=["1", "DENG-NEW-KEY-1111-2222-3333"],
            remote_results=[
                ("check_timeout", LICENSE_CHECK_TIMEOUT_USER_MESSAGE),
                ("active", "ok"),
            ],
        )
        self.assertTrue(ok)
        self.assertEqual(keys[0], "DENG-OLD-KEY-AAAA-BBBB-CCCC")
        self.assertEqual(keys[1], "DENG-NEW-KEY-1111-2222-3333")
        self.assertIn("License check timed out", out)
        self.assertNotIn("License Invalid", out)

    def test_expired_key_retry_prompts_new_key(self):
        ok, _out, keys = self._run_remote_loop(
            inputs=["1", "DENG-NEW-KEY-1111-2222-3333"],
            remote_results=[("expired", "Key expired"), ("active", "ok")],
        )
        self.assertTrue(ok)
        self.assertEqual(keys[1], "DENG-NEW-KEY-1111-2222-3333")

    def test_api_error_retry_prompts_new_key(self):
        ok, _out, keys = self._run_remote_loop(
            inputs=["1", "DENG-NEW-KEY-1111-2222-3333"],
            remote_results=[("server_unavailable", "502 bad gateway"), ("active", "ok")],
        )
        self.assertTrue(ok)
        self.assertEqual(keys[0], "DENG-OLD-KEY-AAAA-BBBB-CCCC")
        self.assertEqual(keys[1], "DENG-NEW-KEY-1111-2222-3333")

    def test_exit_from_every_failure_reason(self):
        for result, msg in [
            ("wrong_device", "w"),
            ("invalid", "bad"),
            ("check_timeout", LICENSE_CHECK_TIMEOUT_USER_MESSAGE),
            ("expired", "expired"),
            ("server_unavailable", "down"),
        ]:
            with self.subTest(result=result):
                ok, out, _keys = self._run_remote_loop(
                    inputs=["0"],
                    remote_results=[(result, msg)],
                )
                self.assertFalse(ok)
                self.assertNotIn("Segmentation fault", out)
                self.assertNotIn("Traceback", out)

    def test_invalid_menu_choice_reprompts(self):
        _ok, out, _keys = self._run_remote_loop(
            inputs=["9", "0"],
            remote_results=[("wrong_device", "w")],
        )
        self.assertGreaterEqual(out.count("1. Enter Different Key"), 2)

    def test_saved_config_cleared_after_failure(self):
        cfg = self._make_cfg("DENG-CACHE-KEY-AAAA-BBBB-CCCC")
        with patch("agent.commands.save_config", side_effect=lambda c: c), \
             patch("agent.license_session.clear_session"):
            cleared = _clear_cached_license_key(cfg)
        self.assertEqual(_load_license_key_from_cfg(cleared), "")

    def test_valid_existing_key_still_passes(self):
        ok, _out, keys = self._run_remote_loop(
            inputs=[],
            remote_results=[("active", "ok")],
        )
        self.assertTrue(ok)
        self.assertEqual(keys, ["DENG-OLD-KEY-AAAA-BBBB-CCCC"])

    def test_legacy_timeout_string_normalized(self):
        result, msg = _normalize_license_check_result(
            "server_unavailable",
            "License check subprocess timed out.",
        )
        self.assertEqual(result, "check_timeout")
        self.assertEqual(msg, LICENSE_CHECK_TIMEOUT_USER_MESSAGE)

    def test_expired_key_message_not_reset_hwid(self):
        _ok, out, _keys = self._run_remote_loop(
            inputs=["0"],
            remote_results=[("expired", "Key expired")],
        )
        self.assertIn("expired", out.lower())
        self.assertNotIn("Reset HWID", out)

    def test_wrong_device_user_message_constant(self):
        self.assertIn("already bound to another device", WRONG_DEVICE_USER_MESSAGE)
        self.assertNotIn("Reset HWID", WRONG_DEVICE_USER_MESSAGE)


if __name__ == "__main__":
    unittest.main()
