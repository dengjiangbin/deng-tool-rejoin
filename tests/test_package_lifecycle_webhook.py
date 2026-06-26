"""Regression tests for Package Dead / Package Recovered Discord webhooks."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import supervisor, webhook

URL = "https://discord.com/api/webhooks/1234567890/secret-token"
PRIVATE_URL = "roblox://navigation/share_links?code=abc123&type=Server"


class PackageLifecycleWebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        self._state_path = webhook.DATA_DIR / "package-lifecycle-webhook-state.json"
        self._state_backup = self._state_path.read_text(encoding="utf-8") if self._state_path.is_file() else None
        self._state_path.unlink(missing_ok=True)

    def tearDown(self) -> None:
        self._state_path.unlink(missing_ok=True)
        if self._state_backup is not None:
            self._state_path.write_text(self._state_backup, encoding="utf-8")

    def _cfg(self, mode: str = "new_post") -> dict:
        return {
            "webhook_mode": mode,
            "webhook_enabled": mode != "none",
            "webhook_url": URL if mode != "none" else "",
            "device_name": "TestPhone",
            "roblox_packages": [{"package": "com.roblox.client", "account_username": "MainUser"}],
        }

    def test_package_dead_embed_is_red_with_required_fields(self) -> None:
        payload = webhook.build_package_lifecycle_embed_payload(
            self._cfg(),
            event="package_dead",
            package="com.roblox.client",
            username="MainUser",
            runtime_seconds=45.0,
        )
        embed = payload["embeds"][0]
        self.assertEqual(embed["title"], "Account Dead")
        self.assertEqual(embed["color"], webhook.EMBED_COLOR_RED)
        names = [field["name"] for field in embed["fields"]]
        self.assertEqual(names, ["Device", "Account", "Username", "Runtime"])
        values = {field["name"]: field["value"] for field in embed["fields"]}
        self.assertEqual(values["Account"], "com.roblox.client")
        self.assertEqual(values["Username"], "||MainUser||")
        self.assertEqual(values["Runtime"], "45s")

    def test_package_recovered_embed_is_green_with_required_fields(self) -> None:
        payload = webhook.build_package_lifecycle_embed_payload(
            self._cfg(),
            event="package_recovered",
            package="com.roblox.client",
            username="MainUser",
        )
        embed = payload["embeds"][0]
        self.assertEqual(embed["title"], "Account Recovered")
        self.assertEqual(embed["color"], webhook.EMBED_COLOR_GREEN)
        self.assertEqual([field["name"] for field in embed["fields"]], ["Device", "Account", "Username"])

    def test_embed_does_not_leak_private_or_webhook_urls(self) -> None:
        cfg = self._cfg()
        cfg["private_server_url"] = PRIVATE_URL
        cfg["webhook_url"] = URL
        for event in ("package_dead", "package_recovered"):
            with self.subTest(event=event):
                payload = webhook.build_package_lifecycle_embed_payload(
                    cfg,
                    event=event,
                    package="com.roblox.client",
                    username="MainUser",
                )
                blob = json.dumps(payload)
                self.assertNotIn(PRIVATE_URL, blob)
                self.assertNotIn("secret-token", blob)
                self.assertNotIn("discord.com/api/webhooks", blob)

    def test_none_mode_does_not_send_or_crash(self) -> None:
        with patch("agent.webhook._discord_json_request") as request:
            ok, message = webhook.send_package_lifecycle_alert(
                self._cfg("none"),
                event="package_dead",
                package="com.roblox.client",
                username="MainUser",
            )
        self.assertFalse(ok)
        self.assertIn("disabled", message)
        request.assert_not_called()

    def test_missing_webhook_url_does_not_crash(self) -> None:
        cfg = self._cfg("new_post")
        cfg["webhook_url"] = ""
        with patch("agent.webhook._discord_json_request") as request:
            ok, message = webhook.send_package_lifecycle_alert(
                cfg,
                event="package_dead",
                package="com.roblox.client",
                username="MainUser",
            )
        self.assertFalse(ok)
        request.assert_not_called()

    def test_new_post_mode_posts_lifecycle_alert(self) -> None:
        with patch("agent.webhook._discord_json_request", return_value=(True, "webhook HTTP 200", "msg-1")) as request:
            ok, _ = webhook.send_package_lifecycle_alert(
                self._cfg("new_post"),
                event="package_dead",
                package="com.roblox.client",
                username="MainUser",
            )
        self.assertTrue(ok)
        self.assertEqual(request.call_args.args[2], "POST")

    def test_edit_mode_still_posts_lifecycle_alert_without_touching_monitor_state(self) -> None:
        cfg = self._cfg("edit")
        cfg["webhook_last_message_id"] = "monitor-message"
        with patch("agent.webhook._discord_json_request", return_value=(True, "webhook HTTP 200", "lifecycle-msg")) as request, \
             patch("agent.config.save_config") as save:
            ok, _ = webhook.send_package_lifecycle_alert(
                cfg,
                event="package_recovered",
                package="com.roblox.client",
                username="MainUser",
            )
        self.assertTrue(ok)
        self.assertEqual(request.call_args.args[2], "POST")
        self.assertEqual(cfg["webhook_last_message_id"], "monitor-message")
        save.assert_not_called()

    def test_dead_transition_marks_state_once(self) -> None:
        self.assertFalse(webhook.package_lifecycle_dead_already_notified("com.roblox.client"))
        webhook.mark_package_lifecycle_dead_notified("com.roblox.client")
        self.assertTrue(webhook.package_lifecycle_dead_already_notified("com.roblox.client"))

    def test_repeated_dead_loop_does_not_resend_after_mark(self) -> None:
        webhook.mark_package_lifecycle_dead_notified("com.roblox.client")
        entry = {"package": "com.roblox.client", "account_username": "MainUser"}
        sup = supervisor.WatchdogSupervisor([entry], self._cfg())
        sup._last_online_ts["com.roblox.client"] = 1.0
        with patch.object(sup, "_in_loading_grace", return_value=False), \
             patch.object(sup, "_in_grace", return_value=False), \
             patch("agent.webhook.send_package_lifecycle_alert") as send:
            sup._maybe_send_package_dead_webhook(
                "com.roblox.client",
                entry,
                supervisor.STATUS_ONLINE,
                supervisor.STATUS_DEAD,
                0.0,
            )
            sup._maybe_send_package_dead_webhook(
                "com.roblox.client",
                entry,
                supervisor.STATUS_DEAD,
                supervisor.STATUS_DEAD,
                0.0,
            )
        send.assert_not_called()

    def test_recovered_requires_prior_dead_state(self) -> None:
        entry = {"package": "com.roblox.client", "account_username": "MainUser"}
        sup = supervisor.WatchdogSupervisor([entry], self._cfg())
        with patch("agent.webhook.send_package_lifecycle_alert") as send:
            sup._maybe_send_package_recovered_webhook("com.roblox.client", entry)
        send.assert_not_called()

    def test_recovered_sends_once_after_dead_and_clears_state(self) -> None:
        webhook.mark_package_lifecycle_dead_notified("com.roblox.client")
        self.assertTrue(webhook.package_lifecycle_recover_pending("com.roblox.client"))
        with patch("agent.webhook._discord_json_request", return_value=(True, "webhook HTTP 200", "msg-2")) as request:
            ok, _ = webhook.send_package_lifecycle_alert(
                self._cfg(),
                event="package_recovered",
                package="com.roblox.client",
                username="MainUser",
            )
        self.assertTrue(ok)
        request.assert_called_once()
        webhook.mark_package_lifecycle_recovered("com.roblox.client")
        self.assertFalse(webhook.package_lifecycle_recover_pending("com.roblox.client"))

    def test_supervisor_dead_notification_skips_launch_grace(self) -> None:
        entry = {"package": "com.roblox.client", "account_username": "MainUser"}
        sup = supervisor.WatchdogSupervisor([entry], self._cfg())
        sup._last_online_ts["com.roblox.client"] = 0.0
        with patch.object(sup, "_in_loading_grace", return_value=True):
            self.assertFalse(
                sup._should_notify_package_dead(
                    "com.roblox.client",
                    supervisor.STATUS_ONLINE,
                    supervisor.STATUS_DEAD,
                    0.0,
                )
            )

    def test_supervisor_dead_notification_fires_once_on_transition(self) -> None:
        entry = {"package": "com.roblox.client", "account_username": "MainUser"}
        sup = supervisor.WatchdogSupervisor([entry], self._cfg())
        sup._last_online_ts["com.roblox.client"] = 1.0
        with patch.object(sup, "_in_loading_grace", return_value=False), \
             patch.object(sup, "_in_grace", return_value=False), \
             patch("agent.webhook.send_package_lifecycle_alert", return_value=(True, "ok")) as send:
            sup._maybe_send_package_dead_webhook(
                "com.roblox.client",
                entry,
                supervisor.STATUS_ONLINE,
                supervisor.STATUS_DEAD,
                0.0,
            )
            sup._maybe_send_package_dead_webhook(
                "com.roblox.client",
                entry,
                supervisor.STATUS_DEAD,
                supervisor.STATUS_DEAD,
                0.0,
            )
        send.assert_called_once()
        self.assertTrue(webhook.package_lifecycle_dead_already_notified("com.roblox.client"))

    def test_supervisor_recovered_only_after_recovery_gate_online(self) -> None:
        entry = {"package": "com.roblox.client", "account_username": "MainUser"}
        sup = supervisor.WatchdogSupervisor([entry], self._cfg())
        webhook.mark_package_lifecycle_dead_notified("com.roblox.client")
        with patch("agent.webhook.send_package_lifecycle_alert", return_value=(True, "ok")) as send:
            sup._maybe_send_package_recovered_webhook("com.roblox.client", entry)
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["event"], "package_recovered")
        self.assertTrue(webhook.package_lifecycle_recover_pending("com.roblox.client") is False)

    def test_periodic_status_still_works_after_lifecycle_send(self) -> None:
        cfg = self._cfg("new_post")
        with patch("agent.webhook._discord_json_request", return_value=(True, "webhook HTTP 200", "lifecycle-msg")) as request:
            webhook.send_package_lifecycle_alert(
                cfg,
                event="package_dead",
                package="com.roblox.client",
                username="MainUser",
            )
            ok, _ = webhook.send_periodic_status(cfg, supervisor_snapshot=[], app_stats={})
        self.assertTrue(ok)
        self.assertEqual(request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
