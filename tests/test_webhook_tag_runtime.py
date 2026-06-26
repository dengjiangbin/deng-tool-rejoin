"""Package Dead runtime formatting and Tag Discord webhook behavior."""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import commands, runtime_format, supervisor, webhook
from agent.config import default_config, validate_config

URL = "https://discord.com/api/webhooks/1234567890/secret-token"
TAG_ID = "123456789012345678"
PKG = "com.moons.litesc"
USER = "denghub2"


class LifecycleDeadRuntimeFormatTests(unittest.TestCase):
    def test_runtime_max_two_units(self) -> None:
        self.assertEqual(runtime_format.format_lifecycle_dead_runtime(45), "45s")
        self.assertEqual(runtime_format.format_lifecycle_dead_runtime(192), "3m 12s")
        self.assertEqual(runtime_format.format_lifecycle_dead_runtime(3844), "1h 04m")
        self.assertEqual(runtime_format.format_lifecycle_dead_runtime(90000), "1d 01h")


class WebhookTagDiscordTests(unittest.TestCase):
    def setUp(self) -> None:
        self._lifecycle_path = webhook.DATA_DIR / "package-lifecycle-webhook-state.json"
        self._lifecycle_backup = (
            self._lifecycle_path.read_text(encoding="utf-8")
            if self._lifecycle_path.is_file()
            else None
        )
        self._lifecycle_path.unlink(missing_ok=True)

    def tearDown(self) -> None:
        self._lifecycle_path.unlink(missing_ok=True)
        if self._lifecycle_backup is not None:
            self._lifecycle_path.write_text(self._lifecycle_backup, encoding="utf-8")

    def _cfg(self, tag_enabled: bool = False) -> dict:
        cfg = {
            "webhook_mode": "new_post",
            "webhook_enabled": True,
            "webhook_url": URL,
            "device_name": "TestPhone",
            "webhook_tag_enabled": tag_enabled,
        }
        if tag_enabled:
            cfg["webhook_tag_user_id"] = TAG_ID
        return cfg

    def test_validate_discord_tag_user_id(self) -> None:
        self.assertEqual(webhook.validate_discord_tag_user_id(TAG_ID), TAG_ID)
        with self.assertRaises(ValueError):
            webhook.validate_discord_tag_user_id("abc")
        with self.assertRaises(ValueError):
            webhook.validate_discord_tag_user_id("123")

    def test_package_dead_with_tag_enabled(self) -> None:
        payload = webhook.build_package_lifecycle_embed_payload(
            self._cfg(True),
            event="package_dead",
            package=PKG,
            username=USER,
            runtime_seconds=45.0,
        )
        send_payload = dict(payload)
        send_payload["allowed_mentions"] = webhook._lifecycle_allowed_mentions(
            self._cfg(True), "package_dead"
        )
        content = webhook._lifecycle_content(self._cfg(True), "package_dead")
        if content:
            send_payload["content"] = content
        self.assertEqual(send_payload["content"], f"<@{TAG_ID}>")
        self.assertEqual(send_payload["allowed_mentions"], {"parse": [], "users": [TAG_ID]})
        names = [f["name"] for f in send_payload["embeds"][0]["fields"]]
        self.assertIn("Runtime", names)
        username_field = next(f for f in send_payload["embeds"][0]["fields"] if f["name"] == "Username")
        self.assertEqual(username_field["value"], "||denghub2||")

    def test_package_dead_with_tag_disabled_no_mention(self) -> None:
        payload = webhook.build_package_lifecycle_embed_payload(
            self._cfg(False),
            event="package_dead",
            package=PKG,
            username=USER,
            runtime_seconds=192.0,
        )
        mentions = webhook._lifecycle_allowed_mentions(self._cfg(False), "package_dead")
        content = webhook._lifecycle_content(self._cfg(False), "package_dead")
        self.assertIsNone(content)
        self.assertEqual(mentions, {"parse": []})
        self.assertNotIn("content", payload)

    def test_package_recovered_never_tags_or_runtime(self) -> None:
        cfg = self._cfg(True)
        payload = webhook.build_package_lifecycle_embed_payload(
            cfg,
            event="package_recovered",
            package=PKG,
            username=USER,
            runtime_seconds=999.0,
        )
        mentions = webhook._lifecycle_allowed_mentions(cfg, "package_recovered")
        content = webhook._lifecycle_content(cfg, "package_recovered")
        self.assertIsNone(content)
        self.assertEqual(mentions, {"parse": []})
        names = [f["name"] for f in payload["embeds"][0]["fields"]]
        self.assertNotIn("Runtime", names)
        self.assertNotIn("content", payload)

    def test_stats_webhook_no_tag_when_tag_enabled(self) -> None:
        cfg = self._cfg(True)
        snapshot = [{
            "package": PKG,
            "username": USER,
            "status": "Online",
            "online_since": 1.0,
        }]
        payload = webhook.build_status_embed_payload(cfg, supervisor_snapshot=snapshot, app_stats={})
        self.assertEqual(payload.get("allowed_mentions"), {"parse": []})
        self.assertNotIn("content", payload)
        blob = json.dumps(payload)
        self.assertNotIn(f"<@{TAG_ID}>", blob)
        self.assertNotIn("@everyone", blob)
        self.assertNotIn("@here", blob)

    def test_runtime_persists_and_resets_after_recover(self) -> None:
        webhook.record_package_lifecycle_alive(PKG, 1000.0)
        secs = webhook.lifecycle_dead_runtime_seconds(PKG, 1045.0)
        self.assertEqual(secs, 45.0)
        webhook.mark_package_lifecycle_recovered(PKG, username=USER)
        row = webhook._load_package_lifecycle_state()["packages"][PKG]
        self.assertNotIn("alive_since", row)
        webhook.record_package_lifecycle_alive(PKG, 2000.0)
        secs2 = webhook.lifecycle_dead_runtime_seconds(PKG, 2192.0)
        self.assertEqual(secs2, 192.0)

    def test_supervisor_dead_includes_runtime_from_persisted_alive(self) -> None:
        webhook.record_package_lifecycle_alive(PKG, 5000.0)
        entry = {"package": PKG, "account_username": USER}
        cfg = self._cfg(False)
        sup = supervisor.WatchdogSupervisor([entry], cfg)
        sup._last_online_ts[PKG] = 5000.0
        with patch.object(sup, "_in_loading_grace", return_value=False), \
             patch.object(sup, "_in_grace", return_value=False), \
             patch("agent.webhook._discord_json_request", return_value=(True, "ok", "m1")) as post:
            sup._maybe_send_package_dead_webhook(
                PKG,
                entry,
                supervisor.STATUS_ONLINE,
                supervisor.STATUS_DEAD,
                5045.0,
            )
        payload = post.call_args.args[1]
        runtime_field = next(f for f in payload["embeds"][0]["fields"] if f["name"] == "Runtime")
        self.assertEqual(runtime_field["value"], "45s")

    def test_config_menu_enable_disable_tag(self) -> None:
        draft: dict = {}
        with patch("agent.commands._prompt", side_effect=["1", TAG_ID]):
            commands._config_webhook_tag_discord(draft)
        self.assertTrue(draft.get("webhook_tag_enabled"))
        self.assertEqual(draft.get("webhook_tag_user_id"), TAG_ID)
        with patch("agent.commands._prompt", return_value="2"):
            commands._config_webhook_tag_discord(draft)
        self.assertFalse(draft.get("webhook_tag_enabled"))
        self.assertNotIn("webhook_tag_user_id", draft)

    def test_invalid_discord_id_rejected_in_menu(self) -> None:
        draft: dict = {}
        with patch("agent.commands._prompt", side_effect=["bad-id", "0"]):
            commands._config_webhook_tag_discord(draft)
        self.assertFalse(draft.get("webhook_tag_enabled"))


class WebhookMenuDisplayTests(unittest.TestCase):
    def _menu_text(self, cfg: dict) -> str:
        with patch("agent.commands._is_interactive", return_value=True), \
             patch("agent.safe_io.safe_prompt", return_value="6"):
            buf = io.StringIO()
            with redirect_stdout(buf):
                commands._config_menu_webhook(cfg)
        return buf.getvalue()

    def test_summary_tag_disabled(self) -> None:
        cfg = validate_config(default_config())
        cfg.update({
            "webhook_mode": "edit",
            "webhook_url": URL,
            "webhook_interval_minutes": 5,
            "webhook_tag_enabled": False,
        })
        text = self._menu_text(cfg)
        self.assertIn("Mode: Edit", text)
        self.assertIn("Interval: 5m", text)
        self.assertIn("URL: configured", text)
        self.assertIn("Tag Discord: Disabled", text)
        self.assertNotIn("Webhook: Edit every 5m", text)
        self.assertNotIn(URL, text)
        self.assertNotIn(TAG_ID, text)

    def test_summary_tag_enabled(self) -> None:
        cfg = validate_config(default_config())
        cfg.update({
            "webhook_mode": "edit",
            "webhook_url": URL,
            "webhook_interval_minutes": 5,
            "webhook_tag_enabled": True,
            "webhook_tag_user_id": TAG_ID,
        })
        text = self._menu_text(cfg)
        self.assertIn("Tag Discord: Enabled", text)
        self.assertNotIn(f"<@{TAG_ID}>", text)
        self.assertNotIn(TAG_ID, text)

    def test_menu_order(self) -> None:
        cfg = validate_config(default_config())
        text = self._menu_text(cfg)
        mode_idx = text.index("1. Mode")
        interval_idx = text.index("2. Interval")
        url_idx = text.index("3. URL")
        tag_idx = text.index("4. Tag Discord")
        test_idx = text.index("5. Test Webhook Now")
        back_idx = text.index("6. Back")
        self.assertLess(mode_idx, interval_idx)
        self.assertLess(interval_idx, url_idx)
        self.assertLess(url_idx, tag_idx)
        self.assertLess(tag_idx, test_idx)
        self.assertLess(test_idx, back_idx)


if __name__ == "__main__":
    unittest.main()
