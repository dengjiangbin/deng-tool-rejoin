import json
import unittest
import unittest.mock

from agent.webhook import (
    WebhookError,
    build_status_embed_payload,
    mask_webhook_url,
    validate_webhook_interval,
    validate_webhook_url,
)


class WebhookTests(unittest.TestCase):
    def test_masks_webhook_url(self):
        masked = mask_webhook_url("https://discord.com/api/webhooks/1234567890/very-secret-token")
        self.assertIn("***MASKED***", masked)
        self.assertNotIn("very-secret-token", masked)

    def test_validates_discord_webhook_url(self):
        self.assertEqual(
            validate_webhook_url("https://discord.com/api/webhooks/1234567890/token"),
            "https://discord.com/api/webhooks/1234567890/token",
        )
        with self.assertRaises(WebhookError):
            validate_webhook_url("https://example.com/api/webhooks/123/token")

    def test_interval_minimum(self):
        self.assertEqual(validate_webhook_interval(5), 5)
        with self.assertRaises(WebhookError):
            validate_webhook_interval(4)


class WebhookStatusEmbedTests(unittest.TestCase):
    """Tests for the simplified Discord status embed."""

    def _base_cfg(self):
        return {
            "device_name": "localhost",
            "agent_version": "1.0.0",
            "roblox_packages": [
                {"package": "com.roblox.client", "account_username": "Main"},
            ],
            "license_key": "DENG-E1320000000096A7",
            "webhook_tags": ["old-noise"],
            "_mem_info": {"free_mb": "4617 MB", "percent_free": "60"},
            "_cpu_pct": "304%",
            "_temp_c": "45.7",
        }

    def test_status_overview_uses_only_online_offline_total(self):
        snapshot = [
            {"package": "com.roblox.client", "username": "Main", "status": "Online"},
            {"package": "com.moons.alt1", "username": "Alt", "status": "Offline"},
            {"package": "com.moons.alt2", "username": "Alt2", "status": "Reviving"},
        ]
        payload = build_status_embed_payload(self._base_cfg(), supervisor_snapshot=snapshot)
        embed = payload["embeds"][0]
        overview = next(f["value"] for f in embed["fields"] if f["name"] == "Status Overview")
        self.assertIn("Online: 1", overview)
        self.assertIn("Offline: 2", overview)
        self.assertIn("Total: 3", overview)
        for label in ("Ready:", "Preparing:", "Warning:", "Failed:"):
            self.assertNotIn(label, overview)

    def test_embed_contract_removes_old_noise(self):
        payload = build_status_embed_payload(self._base_cfg())
        embed = payload["embeds"][0]
        self.assertEqual(embed["title"], "📊 DENG Tool: Rejoin Status Monitor")
        self.assertEqual(embed["url"], "https://aio.deng.my.id")
        self.assertEqual(embed["footer"]["text"], "DENG Tool: Rejoin")
        self.assertNotIn("v1.0.0", embed["footer"]["text"])
        self.assertNotIn("Event: monitor", json.dumps(embed))
        self.assertNotIn("localhost", json.dumps(embed))
        self.assertNotIn("Type:", json.dumps(embed))
        self.assertNotIn("Tags", json.dumps(embed))
        overview = next(f["value"] for f in embed["fields"] if f["name"] == "Status Overview")
        for label in ("Online:", "Offline:", "Total:"):
            self.assertIn(label, overview)
        for label in ("Ready:", "Preparing:", "Warning:", "Failed:"):
            self.assertNotIn(label, overview)

    def test_license_key_not_exposed_in_embed(self):
        payload = build_status_embed_payload(self._base_cfg())
        serialized = json.dumps(payload)
        self.assertNotIn("E1320000000096A7", serialized)
        self.assertIn("DENG-E132", serialized)

    def test_build_does_not_raise_without_app_stats(self):
        try:
            build_status_embed_payload(self._base_cfg())
        except Exception as exc:
            self.fail(f"build_status_embed_payload raised unexpectedly: {exc}")

    def test_application_details_spoiler_wrap_account_username(self):
        cfg = self._base_cfg()
        cfg["roblox_packages"] = [{"package": "com.roblox.client", "account_username": "TraderJoe"}]
        payload = build_status_embed_payload(cfg)
        detail = next(f["value"] for f in payload["embeds"][0]["fields"] if f["name"] == "Application Details")
        self.assertIn("||TraderJoe||", detail)

    def test_device_field_uses_phone_model_without_localhost_or_type(self):
        with unittest.mock.patch("agent.license.get_public_device_model", return_value="Pixel 7 Pro"):
            payload = build_status_embed_payload(self._base_cfg())
        dev = next(f["value"] for f in payload["embeds"][0]["fields"] if f["name"] == "📱 Device")
        self.assertEqual("Pixel 7 Pro", dev)
        self.assertNotIn("localhost", dev)
        self.assertNotIn("Type:", dev)


if __name__ == "__main__":
    unittest.main()
