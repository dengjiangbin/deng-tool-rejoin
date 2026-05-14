import unittest

from agent.config import (
    ConfigError,
    default_config,
    enabled_package_names,
    is_valid_package_name,
    normalize_package_detection_hint,
    validate_config,
)


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
        self.assertEqual(enabled_package_names(validated), ["com.roblox.client"])
        self.assertEqual(validated["roblox_packages"][0]["account_username"], "Main")
        self.assertEqual(validated["roblox_packages"][0]["username_source"], "manual")

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

    def test_migrates_roblox_package_to_roblox_packages(self):
        cfg = default_config()
        cfg.pop("roblox_packages")
        cfg["roblox_package"] = "com.roblox.client.clone1"
        validated = validate_config(cfg)
        self.assertEqual(
            validated["roblox_packages"],
            [{"package": "com.roblox.client.clone1", "account_username": "Main", "enabled": True, "username_source": "manual"}],
        )
        self.assertEqual(validated["roblox_package"], "com.roblox.client.clone1")

    def test_migrates_label_to_account_username(self):
        cfg = default_config()
        cfg["roblox_packages"] = [{"package": "com.roblox.client", "label": "Alt Label", "enabled": True}]
        validated = validate_config(cfg)
        self.assertEqual(validated["roblox_packages"][0]["account_username"], "Alt Label")
        self.assertEqual(validated["roblox_packages"][0]["username_source"], "manual")

    def test_migrates_package_string_list_to_package_objects(self):
        cfg = default_config()
        cfg["roblox_packages"] = ["com.roblox.client", "com.roblox.client.clone1"]
        validated = validate_config(cfg)
        self.assertEqual(
            validated["roblox_packages"],
            [
                {"package": "com.roblox.client", "account_username": "", "enabled": True, "username_source": "not_set"},
                {"package": "com.roblox.client.clone1", "account_username": "", "enabled": True, "username_source": "not_set"},
            ],
        )

    def test_validates_multiple_package_names(self):
        cfg = default_config()
        cfg["roblox_packages"] = [
            {"package": "com.roblox.client", "account_username": "Main", "enabled": True, "username_source": "manual"},
            {"package": "com.roblox.client.clone1", "account_username": "Alt 1", "enabled": True, "username_source": "manual"},
        ]
        validated = validate_config(cfg)
        self.assertEqual(validated["selected_package_mode"], "multiple")
        self.assertEqual(len(validated["roblox_packages"]), 2)
        self.assertEqual(validated["roblox_packages"][1]["account_username"], "Alt 1")
        self.assertIn("moons", validated["package_detection_hints"])

    def test_rejects_invalid_multiple_package_name(self):
        cfg = default_config()
        cfg["roblox_packages"] = ["com.roblox.client", "bad package; rm -rf /"]
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_removes_old_post_launch_action_config(self):
        cfg = default_config()
        cfg["post_launch_action"] = "script_injection"
        validated = validate_config(cfg)
        self.assertNotIn("post_launch_action", validated)

    def test_webhook_interval_validation(self):
        cfg = default_config()
        cfg["webhook_enabled"] = True
        cfg["webhook_interval_seconds"] = 10
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_webhook_disabled_makes_snapshot_inactive(self):
        cfg = default_config()
        cfg["webhook_enabled"] = False
        cfg["webhook_snapshot_enabled"] = True
        cfg["webhook_send_snapshot"] = True
        cfg["webhook_interval_seconds"] = 10
        validated = validate_config(cfg)
        self.assertFalse(validated["webhook_snapshot_enabled"])
        self.assertFalse(validated["webhook_send_snapshot"])

    def test_package_detection_hint_validation(self):
        cfg = default_config()
        cfg["package_detection_hints"] = ["com.moons.*", "Roblox", "bad;rm"]
        with self.assertRaises(ConfigError):
            validate_config(cfg)
        self.assertEqual(normalize_package_detection_hint("com.moons.*"), "com.moons.")


if __name__ == "__main__":
    unittest.main()
