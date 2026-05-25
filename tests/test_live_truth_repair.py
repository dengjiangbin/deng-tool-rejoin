from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from agent import commands
from agent.bootstrap_installer import render_direct_install_bootstrap
from agent.config import default_config, validate_config


class ProbeFirstUploadTests(unittest.TestCase):
    def test_probe_upload_first_run_creates_path_before_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            probe_dir = Path(tmp) / "probes"
            args = argparse.Namespace(upload=True, diag=False)
            out = io.StringIO()
            with mock.patch("agent.probe.PROBE_DIR", probe_dir), \
                 mock.patch("agent.probe.collect_probe", return_value={"probe_version": 1, "errors": []}), \
                 mock.patch("agent.probe.upload_probe", return_value=(True, "p-test123")), \
                 redirect_stdout(out):
                rc = commands.cmd_probe(args)
            self.assertEqual(rc, 0)
            files = list(probe_dir.glob("probe-*.json"))
            self.assertEqual(len(files), 1)
            self.assertEqual(json.loads(files[0].read_text(encoding="utf-8"))["probe_version"], 1)
            text = out.getvalue()
            self.assertIn("probe saved:", text)
            self.assertIn("probe_id: p-test123", text)
            self.assertNotIn("no file path", text.lower())
            self.assertNotIn("Re-run:", text)


class LicensePromptTextTests(unittest.TestCase):
    def _cfg_without_key(self) -> dict:
        cfg = validate_config(default_config())
        cfg["license"]["key"] = ""
        cfg["license_key"] = ""
        return cfg

    def test_no_saved_key_prompt_no_color(self) -> None:
        cfg = self._cfg_without_key()
        out = io.StringIO()
        with mock.patch("agent.commands.load_config", return_value=cfg), \
             mock.patch("agent.commands._is_interactive", return_value=True), \
             mock.patch("agent.commands.safe_io.safe_prompt", return_value=None) as prompt, \
             redirect_stdout(out):
            ok = commands._ensure_remote_license_menu_loop(cfg, argparse.Namespace(), use_color=False)
        self.assertFalse(ok)
        text = out.getvalue()
        self.assertIn("[?] Verifying License:", text)
        self.assertIn("[!] No License Key Found.", text)
        self.assertNotIn("\x1b[", text)
        self.assertIn("Enter License Key", prompt.call_args[0][0])

    def test_no_saved_key_prompt_color(self) -> None:
        cfg = self._cfg_without_key()
        out = io.StringIO()
        with mock.patch("agent.commands.load_config", return_value=cfg), \
             mock.patch("agent.commands._is_interactive", return_value=True), \
             mock.patch("agent.commands.safe_io.safe_prompt", return_value=None), \
             redirect_stdout(out):
            commands._ensure_remote_license_menu_loop(cfg, argparse.Namespace(), use_color=True)
        text = out.getvalue()
        self.assertIn("\033[1;96m[?] Verifying License:", text)
        self.assertIn("\033[1;93m[!] No License Key Found.", text)

    def test_valid_cached_key_still_rechecks_remote_before_menu(self) -> None:
        cfg = self._cfg_without_key()
        cfg["license"]["key"] = "DENG-AAAA-BBBB-CCCC-DDDD"
        with mock.patch("agent.commands.load_config", return_value=cfg), \
             mock.patch("agent.commands._remote_license_run_check", return_value=("active", "ok")) as check:
            out = io.StringIO()
            with redirect_stdout(out):
                ok = commands._ensure_remote_license_menu_loop(cfg, argparse.Namespace(), use_color=False)
        self.assertTrue(ok)
        check.assert_called_once()
        self.assertNotIn("No License Key Found", out.getvalue())


class InstallerUiTests(unittest.TestCase):
    def test_direct_installer_success_ui_is_clean_without_progress_bar(self) -> None:
        script = render_direct_install_bootstrap(
            base_url="https://rejoin.deng.my.id",
            package_sha256="a" * 64,
            banner_lines=("Version: main-dev",),
        )
        self.assertIn("DENG Tool: Rejoin Installing", script)
        self.assertIn("Version: main-dev", script)
        self.assertIn("Preparing secure download", script)
        self.assertIn("Files installed", script)
        self.assertIn("Manifest signature verified", script)
        self.assertIn("Runtime verified", script)
        self.assertIn("Install complete.", script)
        self.assertNotIn("100%", script)
        self.assertNotIn("[################", script)
        self.assertNotIn("[------", script)
        self.assertNotIn("DENG Tool: Rejoin Installed", script)
        self.assertNotIn("Next: deng-rejoin", script)

    def test_direct_installer_failure_does_not_say_complete_before_verification(self) -> None:
        script = render_direct_install_bootstrap(
            base_url="https://rejoin.deng.my.id",
            package_sha256="a" * 64,
        )
        self.assertLess(script.index('"$BIN/deng-rejoin" version'), script.index("Install complete."))
        self.assertNotIn("Re-download the installer:", script)


if __name__ == "__main__":
    unittest.main()
