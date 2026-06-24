"""Runtime-entrypoint regressions for the test-channel webhook migration."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import commands, probe
from agent.config import default_config, validate_config


class WebhookCliRecoveryTests(unittest.TestCase):
    def test_legacy_webhook_config_migrates_without_failure(self) -> None:
        cfg = default_config()
        cfg.update({"webhook_enabled": True, "webhook_mode": "edit_message", "webhook_url": "https://discord.com/api/webhooks/123/token", "webhook_interval_seconds": 300})
        cfg.pop("webhook_interval_minutes", None)
        migrated = validate_config(cfg)
        self.assertEqual(migrated["webhook_mode"], "edit")
        self.assertEqual(migrated["webhook_interval_minutes"], 5)

    def test_real_main_menu_path_does_not_emit_internal_error_for_legacy_config(self) -> None:
        cfg = default_config()
        cfg.update({"webhook_enabled": True, "webhook_mode": "new_message", "webhook_url": "https://discord.com/api/webhooks/123/token"})
        cfg["license"]["enabled"] = False
        with tempfile.TemporaryDirectory() as temp, \
             patch("agent.commands.load_config", return_value=validate_config(cfg)), \
             patch("agent.commands._ensure_install_id_saved", side_effect=lambda x: x), \
             patch("agent.commands.run_menu", return_value=0), \
             patch.object(commands.keystore, "DEV_MODE", True), \
             patch("agent.commands.DATA_DIR", Path(temp)):
            self.assertEqual(commands.main([]), 0)

    def test_payload_clamp_keeps_runtime_failure_evidence(self) -> None:
        huge = "x" * 300_000
        payload = {
            "probe_version": 1, "captured_at_iso": "now", "summary": {}, "errors": [],
            "logs": huge, "latest_crash_log": {"tail": "Traceback: exact failure"},
            "installed_build": {"artifact_sha256": "abc"}, "wrapper": {"path": "deng-rejoin"},
            "last_start_diagnostics": {"phase": "menu"}, "start_crash_state": {"last_start_step": "menu"},
            "last_failing_command": {"command": "menu"}, "dumpsys_global": {"window_windows_full": huge},
        }
        clamped = probe.clamp_probe_payload_size(payload, max_raw_bytes=20_000)
        for key in probe._PROBE_PINNED_FIELDS:
            self.assertNotEqual(clamped.get(key), "<dropped: payload size budget>")
        self.assertIn("Traceback", clamped["latest_crash_log"]["tail"])


if __name__ == "__main__":
    unittest.main()
