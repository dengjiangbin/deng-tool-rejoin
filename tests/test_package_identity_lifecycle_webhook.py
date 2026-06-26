"""Lifecycle webhook username resolution and Discord spoiler formatting."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import package_identity, supervisor, webhook

URL = "https://discord.com/api/webhooks/1234567890/secret-token"
PKG_C = "com.moons.litesc"
PKG_D = "com.moons.litesd"
USER_C = "denghub2"
USER_D = "Arayaaa_30"


class PackageIdentityLifecycleWebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        self._lifecycle_path = webhook.DATA_DIR / "package-lifecycle-webhook-state.json"
        self._identity_path = package_identity._IDENTITY_PATH
        self._lifecycle_backup = (
            self._lifecycle_path.read_text(encoding="utf-8")
            if self._lifecycle_path.is_file()
            else None
        )
        self._identity_backup = (
            self._identity_path.read_text(encoding="utf-8")
            if self._identity_path.is_file()
            else None
        )
        self._lifecycle_path.unlink(missing_ok=True)
        self._identity_path.unlink(missing_ok=True)

    def tearDown(self) -> None:
        self._lifecycle_path.unlink(missing_ok=True)
        self._identity_path.unlink(missing_ok=True)
        if self._lifecycle_backup is not None:
            self._lifecycle_path.write_text(self._lifecycle_backup, encoding="utf-8")
        if self._identity_backup is not None:
            self._identity_path.write_text(self._identity_backup, encoding="utf-8")

    def _cfg(self) -> dict:
        return {
            "webhook_mode": "new_post",
            "webhook_enabled": True,
            "webhook_url": URL,
            "device_name": "TestPhone",
            "roblox_packages": [
                {"package": PKG_C, "account_username": ""},
                {"package": PKG_D, "account_username": ""},
            ],
        }

    def _spoiler(self, username: str) -> str:
        return package_identity.format_discord_username_spoiler(username)

    def test_dead_embed_uses_spoiler_username_denghub2(self) -> None:
        package_identity.record_package_identity(PKG_C, USER_C, source="scanner")
        payload = webhook.build_package_lifecycle_embed_payload(
            self._cfg(),
            event="package_dead",
            package=PKG_C,
            username=USER_C,
        )
        values = {f["name"]: f["value"] for f in payload["embeds"][0]["fields"]}
        self.assertEqual(values["Username"], self._spoiler(USER_C))
        self.assertEqual(values["Username"], "||denghub2||")
        blob = json.dumps(payload)
        self.assertNotIn("Unknown", blob)
        self.assertNotIn("N/A", blob)
        self.assertNotIn("unavailable", blob.lower())

    def test_recovered_embed_uses_spoiler_username_arayaaa(self) -> None:
        package_identity.record_package_identity(PKG_D, USER_D, source="scanner")
        payload = webhook.build_package_lifecycle_embed_payload(
            self._cfg(),
            event="package_recovered",
            package=PKG_D,
            username=USER_D,
        )
        values = {f["name"]: f["value"] for f in payload["embeds"][0]["fields"]}
        self.assertEqual(values["Username"], "||Arayaaa_30||")

    def test_dead_after_process_uses_persisted_identity(self) -> None:
        package_identity.record_package_identity(PKG_C, USER_C, source="scanner")
        entry = {"package": PKG_C, "account_username": ""}
        sup = supervisor.WatchdogSupervisor([entry], self._cfg())
        sup._last_online_ts[PKG_C] = 1.0
        with patch.object(sup, "_in_loading_grace", return_value=False), \
             patch.object(sup, "_in_grace", return_value=False), \
             patch("agent.webhook._discord_json_request", return_value=(True, "ok", "m1")) as post:
            sup._maybe_send_package_dead_webhook(
                PKG_C,
                entry,
                supervisor.STATUS_ONLINE,
                supervisor.STATUS_DEAD,
                0.0,
            )
        post.assert_called_once()
        payload = post.call_args.args[1]
        username_field = next(f for f in payload["embeds"][0]["fields"] if f["name"] == "Username")
        self.assertEqual(username_field["value"], "||denghub2||")

    def test_persisted_identity_survives_restart(self) -> None:
        package_identity.record_package_identity(PKG_D, USER_D, source="scanner")
        # Simulate restart: new module load reads same file
        row = package_identity.get_package_identity(PKG_D)
        self.assertIsNotNone(row)
        self.assertEqual(row.get("roblox_username"), USER_D)
        username, source = package_identity.resolve_lifecycle_username(PKG_D)
        self.assertEqual(username, USER_D)
        self.assertEqual(source, "package_identity_cache")

    def test_no_username_never_sends_webhook(self) -> None:
        entry = {"package": PKG_C, "account_username": ""}
        sup = supervisor.WatchdogSupervisor([entry], self._cfg())
        sup._last_online_ts[PKG_C] = 1.0
        with patch.object(sup, "_in_loading_grace", return_value=False), \
             patch.object(sup, "_in_grace", return_value=False), \
             patch("agent.webhook._discord_json_request") as post:
            sup._maybe_send_package_dead_webhook(
                PKG_C,
                entry,
                supervisor.STATUS_ONLINE,
                supervisor.STATUS_DEAD,
                0.0,
            )
        post.assert_not_called()
        self.assertFalse(webhook.package_lifecycle_dead_already_notified(PKG_C))
        state = webhook._load_package_lifecycle_state()["packages"].get(PKG_C, {})
        self.assertTrue(state.get("username_resolution_failed"))

    def test_send_skips_blank_username(self) -> None:
        ok, msg = webhook.send_package_lifecycle_alert(
            self._cfg(),
            event="package_dead",
            package=PKG_C,
            username="",
        )
        self.assertFalse(ok)
        self.assertEqual(msg, "username_resolution_failed")

    def test_supervisor_recovered_sends_spoiler_username(self) -> None:
        package_identity.record_package_identity("com.roblox.client", "MainUser", source="test")
        webhook.mark_package_lifecycle_dead_notified("com.roblox.client", username="MainUser")
        entry = {"package": "com.roblox.client", "account_username": ""}
        sup = supervisor.WatchdogSupervisor([entry], self._cfg())
        with patch("agent.webhook._discord_json_request", return_value=(True, "ok", "m2")) as post:
            sup._maybe_send_package_recovered_webhook("com.roblox.client", entry)
        post.assert_called_once()
        payload = post.call_args.args[1]
        username_field = next(f for f in payload["embeds"][0]["fields"] if f["name"] == "Username")
        self.assertEqual(username_field["value"], "||MainUser||")


if __name__ == "__main__":
    unittest.main()
