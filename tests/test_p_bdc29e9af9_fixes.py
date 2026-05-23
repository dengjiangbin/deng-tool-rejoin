"""Probe p-bdc29e9af9 fixes: HWID validate-only, menu placement, Auto Execute input."""

from __future__ import annotations

import argparse
import inspect
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import commands, menu, safe_io, termux_ui
from agent.config import default_config, validate_config
from agent.license import HWID_RESET_REENTRY_MESSAGE, hash_license_key, mask_license_key, normalize_license_key
from agent.license_store import (
    LocalJsonLicenseStore,
    RESULT_ACTIVE,
    RESULT_REQUIRES_MANUAL_REBIND,
)


def _args(**kw) -> argparse.Namespace:
    ns = argparse.Namespace(no_color=True, verbose=False, debug=False, lines=50)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _tmp_store() -> LocalJsonLicenseStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    Path(tmp.name).unlink()
    return LocalJsonLicenseStore(Path(tmp.name))


def _bound_store_after_reset(uid: str = "u_probe"):
    store = _tmp_store()
    store.get_or_create_user(uid)
    full_key = store.create_key_for_user(uid)
    store.bind_or_check_device(full_key, "aa" * 32, "Pixel 6", "1.0")
    key_hash = hash_license_key(normalize_license_key(full_key))
    store.reset_hwid(uid, key_hash)
    return store, full_key, key_hash


class TestValidateOnlyBinding(unittest.TestCase):
    def test_cached_key_still_bound_allows_active(self):
        store = _tmp_store()
        uid = "bound1"
        store.get_or_create_user(uid)
        full_key = store.create_key_for_user(uid)
        store.bind_or_check_device(full_key, "bb" * 32, "Pixel", "1.0")
        result = store.validate_existing_binding(full_key, "bb" * 32, "Pixel", "1.0")
        self.assertEqual(result, RESULT_ACTIVE)

    def test_cached_key_after_hwid_reset_blocked_on_validate_only(self):
        store, full_key, _ = _bound_store_after_reset()
        result = store.validate_existing_binding(full_key, "bb" * 32, "Pixel", "1.0")
        self.assertEqual(result, RESULT_REQUIRES_MANUAL_REBIND)

    def test_validate_only_does_not_reactivate_binding(self):
        store, full_key, key_hash = _bound_store_after_reset()
        store.validate_existing_binding(full_key, "bb" * 32, "Pixel", "1.0")
        db = store._load()
        self.assertFalse(db["bindings"][key_hash].get("is_active"))

    def test_manual_bind_after_reset_allowed_once(self):
        store, full_key, key_hash = _bound_store_after_reset()
        result = store.bind_or_check_device(full_key, "bb" * 32, "Pixel", "1.0")
        self.assertEqual(result, RESULT_ACTIVE)
        db = store._load()
        self.assertTrue(db["bindings"][key_hash].get("is_active"))

    def test_validate_only_does_not_mutate_binding_row(self):
        store, full_key, key_hash = _bound_store_after_reset()
        before = store._load()["bindings"][key_hash].copy()
        result = store.validate_existing_binding(full_key, "cc" * 32, "Pixel", "1.0")
        after = store._load()["bindings"][key_hash]
        self.assertEqual(result, RESULT_REQUIRES_MANUAL_REBIND)
        self.assertEqual(before, after)


class TestStartupLicenseGate(unittest.TestCase):
    def setUp(self) -> None:
        commands._license_session_validated = False

    def test_cached_key_after_reset_blocks_menu_and_clears_key(self):
        cfg = default_config()
        cfg.setdefault("license", {})["key"] = "DENG-AAAA-BBBB-CCCC-DDDD"
        saved_keys: list[str] = []

        def fake_save(updated):
            lic = updated.setdefault("license", {})
            saved_keys.append(lic.get("key", ""))
            return updated

        out = io.StringIO()
        with patch("agent.commands.load_config", side_effect=lambda: dict(cfg)), \
             patch("agent.commands.save_config", side_effect=fake_save), \
             patch("agent.commands._ensure_install_id_saved", side_effect=lambda x: x), \
             patch("agent.commands._is_interactive", return_value=True), \
             patch("agent.commands._remote_license_run_check", return_value=(RESULT_REQUIRES_MANUAL_REBIND, HWID_RESET_REENTRY_MESSAGE)), \
             patch("agent.commands.safe_io.safe_prompt", return_value=None), \
             redirect_stdout(out):
            ok = commands._ensure_remote_license_menu_loop(cfg, _args(), False)

        self.assertFalse(ok)
        self.assertIn(HWID_RESET_REENTRY_MESSAGE, out.getvalue())
        self.assertIn("", saved_keys)

    def test_startup_validate_only_uses_check_not_bind(self):
        cfg = default_config()
        cfg.setdefault("license", {})["key"] = "DENG-1111-2222-3333-4444"
        seen: list[str] = []

        with patch("agent.commands.load_config", return_value=cfg), \
             patch("agent.commands.save_config", side_effect=lambda x: x), \
             patch("agent.commands._ensure_install_id_saved", side_effect=lambda x: x), \
             patch("agent.commands._is_interactive", return_value=True), \
             patch("agent.commands._remote_license_run_check", side_effect=lambda _c: seen.append("check") or ("active", "ok")), \
             patch("agent.commands._remote_license_run_bind", side_effect=lambda _c: seen.append("bind") or ("active", "ok")):
            ok = commands._ensure_remote_license_menu_loop(cfg, _args(), False)
        self.assertTrue(ok)
        self.assertEqual(seen, ["check"])

    def test_manual_key_entry_uses_bind_endpoint(self):
        cfg = default_config()
        seen: list[str] = []

        prompts = iter(["DENG-AAAA-BBBB-CCCC-DDDD"])
        with patch("agent.commands.load_config", return_value=default_config()), \
             patch("agent.commands.save_config", side_effect=lambda x: x), \
             patch("agent.commands._ensure_install_id_saved", side_effect=lambda x: x), \
             patch("agent.commands._is_interactive", return_value=True), \
             patch("agent.commands._remote_license_run_check", side_effect=lambda _c: seen.append("check") or ("active", "ok")), \
             patch("agent.commands._remote_license_run_bind", side_effect=lambda _c: seen.append("bind") or ("active", "ok")), \
             patch("agent.commands.safe_io.safe_prompt", side_effect=lambda *a, **k: next(prompts)):
            ok = commands._ensure_remote_license_menu_loop(cfg, _args(), False)
        self.assertTrue(ok)
        self.assertEqual(seen, ["bind"])

    def test_key_masked_in_helper(self):
        masked = mask_license_key("DENG-68C9-0BA2-F745-E506")
        self.assertNotIn("68C9-0BA2-F745-E506", masked)
        self.assertIn("...", masked)


class TestTopMenuPlacement(unittest.TestCase):
    def test_top_menu_has_exact_four_options(self):
        labels = [item[1] for item in menu.MENU_ITEMS]
        numbers = [item[0] for item in menu.MENU_ITEMS]
        self.assertEqual(numbers, ["1", "2", "3", "0"])
        self.assertNotIn("Key", labels)
        self.assertNotIn("Auto Execute", labels)
        self.assertNotIn("Package Key", labels)

    def test_setup_config_has_auto_execute_as_option_4(self):
        src = inspect.getsource(commands._run_edit_config_menu)
        ui_src = inspect.getsource(termux_ui.print_config_menu)
        self.assertIn("print_config_menu", src)
        self.assertIn('menu_number("4", "Auto Execute")', ui_src)
        self.assertNotIn('"4. Key"', ui_src)
        self.assertNotIn('"5. Auto Execute"', ui_src)

    def test_first_time_setup_mentions_auto_execute(self):
        src = inspect.getsource(commands._run_first_time_setup_wizard)
        self.assertIn("Auto Execute", src)
        self.assertIn("Step 7 of 8: Auto Execute (Optional)", src)

    def test_handlers_include_package_key_not_in_top_menu(self):
        menu_commands = {item[2] for item in menu.MENU_ITEMS}
        self.assertNotIn("package-key", menu_commands)
        self.assertNotIn("auto-execute", menu_commands)
        self.assertIn("package-key", commands._handlers())


class TestAutoExecuteKaeruInput(unittest.TestCase):
    def test_add_script_prompt_capitalization(self):
        cfg = {"auto_execute_scripts": []}
        prompts = iter(["1", "Y", "print(1)", "END", "N", "0"])
        prompt_texts: list[str] = []

        def fake_prompt(prompt="", **_kwargs):
            prompt_texts.append(prompt)
            return next(prompts)

        with patch("agent.commands._is_interactive", return_value=True), \
             patch("agent.commands.safe_io.safe_prompt", side_effect=fake_prompt), \
             patch("agent.commands.safe_io.press_enter"), \
             patch("agent.commands.save_config", side_effect=lambda c: c), \
             redirect_stdout(io.StringIO()):
            result = commands._config_menu_auto_execute(cfg)

        self.assertEqual(result["auto_execute_scripts"], ["print(1)"])
        self.assertTrue(any("Add Script #1? (Y/N)" in p for p in prompt_texts))
        self.assertTrue(any("Add Script #2? (Y/N)" in p for p in prompt_texts))

    def test_multiline_script_preserves_blank_lines_without_end(self):
        cfg = {"auto_execute_scripts": []}
        lines = ["1", "Y", "line1", "", "line3", "END", "N", "0"]
        prompts = iter(lines)

        with patch("agent.commands._is_interactive", return_value=True), \
             patch("agent.commands.safe_io.safe_prompt", side_effect=lambda *a, **k: next(prompts)), \
             patch("agent.commands.safe_io.press_enter"), \
             patch("agent.commands.save_config", side_effect=lambda c: c), \
             redirect_stdout(io.StringIO()):
            result = commands._config_menu_auto_execute(cfg)

        self.assertEqual(result["auto_execute_scripts"], ["line1\n\nline3"])

    def test_empty_script_rejected(self):
        out = io.StringIO()
        with patch("agent.commands.safe_io.safe_prompt", side_effect=["", "END"]), \
             redirect_stdout(out):
            script = commands._read_auto_execute_script(1)
        self.assertEqual(script, "")
        self.assertIn("Script Cannot Be Empty.", out.getvalue())

    def test_eof_cancels_script_input(self):
        with patch("agent.commands.safe_io.safe_prompt", return_value=None):
            self.assertIsNone(commands._read_auto_execute_script(1))


class TestTopMenuExit(unittest.TestCase):
    def test_option_zero_exits_cleanly(self):
        cfg = validate_config(default_config())
        cfg["roblox_packages"] = [{"package": "com.roblox.client", "enabled": True}]
        with patch("agent.menu.load_config", return_value=cfg), \
             patch("agent.menu.print_banner"), \
             patch("agent.menu._is_interactive", return_value=True), \
             patch("agent.menu.safe_io.safe_prompt", side_effect=["0"]), \
             patch("agent.commands._termux_exit_clean") as hard_exit, \
             redirect_stdout(io.StringIO()):
            rc = commands._run_top_menu_with_clean_exit(_args())
        self.assertEqual(rc, 0)
        hard_exit.assert_called_once()

    def test_eof_from_menu_returns_zero(self):
        cfg = validate_config(default_config())
        cfg["roblox_packages"] = [{"package": "com.roblox.client", "enabled": True}]
        out = io.StringIO()
        with patch("agent.menu.load_config", return_value=cfg), \
             patch("agent.menu.print_banner"), \
             patch("agent.menu._is_interactive", return_value=True), \
             patch("agent.menu.safe_io.safe_prompt", return_value=None), \
             redirect_stdout(out):
            rc = menu.run_menu(_args(), commands._handlers())
        self.assertEqual(rc, 0)
        self.assertIn("Goodbye.", out.getvalue())

    def test_termux_exit_clean_noops_off_termux(self):
        with patch.dict("os.environ", {}, clear=True):
            safe_io.termux_exit_clean()


class TestMenuExit(unittest.TestCase):
    def test_run_menu_does_not_call_termux_exit_clean(self):
        src = inspect.getsource(menu.run_menu)
        self.assertNotIn("termux_exit_clean", src)


class TestValidateConfig(unittest.TestCase):
    def test_default_config_validates(self):
        cfg = default_config()
        validate_config(cfg)


if __name__ == "__main__":
    unittest.main()
