import unittest
import unittest.mock

from agent.webhook import mask_webhook_url, validate_webhook_interval, validate_webhook_url, WebhookError, build_status_embed_payload


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
        self.assertEqual(validate_webhook_interval(30), 30)
        with self.assertRaises(WebhookError):
            validate_webhook_interval(29)


class WebhookStatusEmbedTests(unittest.TestCase):
    """Tests for the full 7-category status overview in build_status_embed_payload."""

    def _base_cfg(self):
        return {
            "device_name": "test-device",
            "agent_version": "1.0.0",
            "roblox_packages": [
                {"package": "com.roblox.client", "account_username": "Main"},
            ],
            "license_key": "",
            "webhook_tags": [],
        }

    def test_status_overview_uses_supervisor_snapshot(self):
        """Webhook embed status overview uses supervisor_snapshot for full counts."""
        snapshot = [
            {"package": "com.roblox.client", "username": "Main", "status": "Online"},
            {"package": "com.moons.alt1",    "username": "Alt",  "status": "Offline"},
            {"package": "com.moons.alt2",    "username": "Alt2", "status": "Reviving"},
        ]
        payload = build_status_embed_payload(self._base_cfg(), supervisor_snapshot=snapshot)
        embed = payload["embeds"][0]
        overview_field = next(f for f in embed["fields"] if "Status Overview" in f["name"])
        overview = overview_field["value"]
        self.assertIn("Online: 1", overview)
        self.assertIn("Offline: 1", overview)
        self.assertIn("Warning: 1", overview)

    def test_status_overview_has_all_seven_categories(self):
        """Status overview always includes all 7 category lines."""
        payload = build_status_embed_payload(self._base_cfg())
        embed = payload["embeds"][0]
        overview_field = next(f for f in embed["fields"] if "Status Overview" in f["name"])
        overview = overview_field["value"]
        for label in ("Online:", "Ready:", "Preparing:", "Warning:", "Offline:", "Failed:", "Total:"):
            self.assertIn(label, overview, msg=f"Missing '{label}' in overview")

    def test_license_key_not_exposed_in_embed(self):
        """The full license key is never included in the webhook embed."""
        cfg = self._base_cfg()
        cfg["license_key"] = "DENG-38AB1234CD56EF78"
        payload = build_status_embed_payload(cfg)
        import json
        serialized = json.dumps(payload)
        self.assertNotIn("38AB1234CD56EF78", serialized)

    def test_build_does_not_raise_without_app_stats(self):
        """build_status_embed_payload does not raise when app_stats is omitted."""
        try:
            build_status_embed_payload(self._base_cfg())
        except Exception as exc:
            self.fail(f"build_status_embed_payload raised unexpectedly: {exc}")

    def test_application_details_reflect_account_username(self):
        cfg = self._base_cfg()
        cfg["roblox_packages"] = [{"package": "com.roblox.client", "account_username": "TraderJoe"}]
        payload = build_status_embed_payload(cfg)
        fields = payload["embeds"][0]["fields"]
        detail = next(f["value"] for f in fields if f["name"] == "Application Details")
        self.assertIn("TraderJoe", detail)

    def test_application_details_unknown_when_account_username_empty(self):
        cfg = self._base_cfg()
        cfg["roblox_packages"] = [{"package": "com.other.app", "account_username": ""}]
        payload = build_status_embed_payload(cfg)
        detail = next(f["value"] for f in payload["embeds"][0]["fields"] if f["name"] == "Application Details")
        self.assertIn("Unknown", detail)


if __name__ == "__main__":
    unittest.main()

