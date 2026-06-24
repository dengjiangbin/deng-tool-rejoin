"""Focused test-channel coverage for periodic Discord status monitoring."""

from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from agent import commands, webhook


URL = "https://discord.com/api/webhooks/1234567890/secret-token"


class WebhookTestChannelTests(unittest.TestCase):
    def _cfg(self, mode: str = "edit") -> dict:
        return {
            "webhook_mode": mode,
            "webhook_enabled": mode != "none",
            "webhook_url": URL if mode != "none" else "",
            "webhook_interval_minutes": 5,
            "webhook_last_message_id": "old-message",
            "roblox_packages": [{"package": "com.example.clone", "account_username": "Main"}],
        }

    def test_setup_and_edit_menu_expose_webhook(self) -> None:
        self.assertIn("_setup_webhook(draft)", inspect.getsource(commands._run_first_time_setup_wizard))
        self.assertIn('menu_number("3", "Webhook")', inspect.getsource(commands.termux_ui.print_config_menu))

    def test_modes_and_interval_bounds(self) -> None:
        self.assertEqual(webhook.WEBHOOK_MODES, {"edit", "new_post", "none"})
        self.assertEqual(webhook.validate_webhook_interval(5), 5)
        self.assertEqual(webhook.validate_webhook_interval(1440), 1440)
        for bad in (4, 1441, "5.5", "five"):
            with self.subTest(bad=bad):
                with self.assertRaises(webhook.WebhookError):
                    webhook.validate_webhook_interval(bad)

    def test_masked_url_never_contains_token(self) -> None:
        masked = webhook.mask_webhook_url(URL)
        self.assertNotIn("secret-token", masked)
        self.assertIn("***MASKED***", masked)

    def test_none_never_sends(self) -> None:
        with patch("agent.webhook._discord_json_request") as request:
            ok, _ = webhook.send_periodic_status(self._cfg("none"), supervisor_snapshot=[], app_stats={})
        self.assertFalse(ok)
        request.assert_not_called()

    def test_edit_mode_edits_existing_message(self) -> None:
        cfg = self._cfg("edit")
        with patch("agent.webhook._discord_json_request", return_value=(True, "ok", None)) as request:
            ok, _ = webhook.send_periodic_status(cfg, supervisor_snapshot=[], app_stats={})
        self.assertTrue(ok)
        self.assertEqual(request.call_args.args[2], "PATCH")

    def test_edit_mode_falls_back_to_new_post_and_stores_id(self) -> None:
        cfg = self._cfg("edit")
        with patch("agent.webhook._discord_json_request", side_effect=[(False, "webhook HTTP 404", None), (True, "ok", "new-message")]) as request:
            ok, _ = webhook.send_periodic_status(cfg, supervisor_snapshot=[], app_stats={})
        self.assertTrue(ok)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.args[2], "POST")
        self.assertEqual(cfg["webhook_last_message_id"], "new-message")

    def test_new_post_always_posts(self) -> None:
        cfg = self._cfg("new_post")
        with patch("agent.webhook._discord_json_request", return_value=(True, "ok", "new")) as request:
            webhook.send_periodic_status(cfg, supervisor_snapshot=[], app_stats={})
        self.assertEqual(request.call_args.args[2], "POST")

    def test_embed_contains_required_monitor_data_with_compact_runtime(self) -> None:
        cfg = self._cfg("new_post")
        cfg.update({"_mem_info": {"free_mb": 612, "percent_free": 15}, "_cpu_pct": 68.0, "_temp_c": 36})
        snapshot = [{"package": "com.example.clone", "username": "Main", "status": "Online", "online_since": 0, "ram_mb": "521"}]
        payload = webhook.build_status_embed_payload(cfg, supervisor_snapshot=snapshot, app_stats={"com.example.clone": {"online": True, "memory_mb": 521, "cpu_pct": 42.0}})
        fields = {field["name"]: field["value"] for field in payload["embeds"][0]["fields"]}
        self.assertTrue(any("System Stats" in name for name in fields))
        self.assertIn("Status Overview", fields)
        self.assertIn("Application Details", fields)
        self.assertIn("521 MB", fields["Application Details"])

    def test_reporter_is_started_only_in_start_monitoring_path(self) -> None:
        source = inspect.getsource(commands.cmd_start)
        self.assertIn("WebhookStatusReporter", source)
        self.assertIn("_webhook_reporter.start()", source)
        self.assertIn("_webhook_reporter.stop()", source)


if __name__ == "__main__":
    unittest.main()
