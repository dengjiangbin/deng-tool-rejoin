import unittest

from agent.url_utils import (
    UrlValidationError,
    detect_launch_mode_from_url,
    mask_launch_url,
    validate_launch_url,
)


class UrlUtilsTests(unittest.TestCase):
    def test_masks_private_server_query_values(self):
        url = "https://www.roblox.com/games/123/name?privateServerLinkCode=abcdef&placeId=123&code=secret"
        masked = mask_launch_url(url)
        self.assertIn("privateServerLinkCode=***MASKED***", masked)
        self.assertIn("placeId=123", masked)
        self.assertIn("code=***MASKED***", masked)
        self.assertNotIn("abcdef", masked)
        self.assertNotIn("secret", masked)

    def test_validates_approved_roblox_urls(self):
        self.assertTrue(validate_launch_url("roblox://experiences/start?placeId=123", "deeplink").valid)
        self.assertTrue(validate_launch_url("https://www.roblox.com/games/123/name", "web_url").valid)
        self.assertTrue(validate_launch_url("https://roblox.com/share?code=abc", "web_url").valid)

    def test_rejects_unapproved_hosts(self):
        with self.assertRaises(UrlValidationError):
            validate_launch_url("https://example.com/games/123", "web_url")

    def test_detects_launch_mode(self):
        self.assertEqual(detect_launch_mode_from_url(""), "app")
        self.assertEqual(detect_launch_mode_from_url("roblox://experiences/start?placeId=1"), "deeplink")
        self.assertEqual(detect_launch_mode_from_url("https://www.roblox.com/games/1"), "web_url")


if __name__ == "__main__":
    unittest.main()
