import unittest

from agent.webhook import mask_webhook_url, validate_webhook_interval, validate_webhook_url, WebhookError


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


if __name__ == "__main__":
    unittest.main()
