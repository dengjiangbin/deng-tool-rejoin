import unittest

from agent.config import ConfigError, default_config, is_valid_package_name, validate_config


class ConfigTests(unittest.TestCase):
    def test_android_package_name_validation(self):
        self.assertTrue(is_valid_package_name("com.roblox.client"))
        self.assertTrue(is_valid_package_name("com.example.roblox_beta"))
        self.assertFalse(is_valid_package_name("com.roblox.client;rm -rf /"))
        self.assertFalse(is_valid_package_name("roblox"))

    def test_valid_config_accepts_web_url(self):
        cfg = default_config()
        cfg.update(
            {
                "launch_mode": "web_url",
                "launch_url": "https://www.roblox.com/games/123/name?privateServerLinkCode=abcdef",
                "reconnect_delay_seconds": 5,
                "health_check_interval_seconds": 10,
                "foreground_grace_seconds": 10,
                "backoff_min_seconds": 10,
                "backoff_max_seconds": 60,
            }
        )
        validated = validate_config(cfg)
        self.assertEqual(validated["launch_mode"], "web_url")
        self.assertIn("android_release", validated)
        self.assertIn("android_sdk", validated)
        self.assertIn("download_dir", validated)

    def test_rejects_bad_launch_mode(self):
        cfg = default_config()
        cfg["launch_mode"] = "macro"
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_rejects_too_fast_delay(self):
        cfg = default_config()
        cfg["reconnect_delay_seconds"] = 1
        with self.assertRaises(ConfigError):
            validate_config(cfg)


if __name__ == "__main__":
    unittest.main()
