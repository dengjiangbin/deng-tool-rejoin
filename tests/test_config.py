import unittest

from agent.config import (
    ConfigError,
    default_config,
    enabled_package_names,
    effective_private_server_url,
    is_valid_package_name,
    normalize_package_detection_hint,
    private_url_launch_context,
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
        self.assertEqual(validated["roblox_packages"][0]["account_username"], "")
        self.assertEqual(validated["roblox_packages"][0]["username_source"], "not_set")

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
        pkg = validated["roblox_packages"][0]
        self.assertEqual(pkg["package"], "com.roblox.client.clone1")
        self.assertEqual(pkg["account_username"], "Main")
        self.assertEqual(pkg["username_source"], "manual")
        self.assertEqual(pkg["roblox_user_id"], 0)
        self.assertIn("account_mapping_source", pkg)
        self.assertIn("account_mapping_status", pkg)
        self.assertIn("account_mapping_updated_at", pkg)
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
        _mapping_defaults = {
            "account_mapping_source": "",
            "account_mapping_status": "Not Mapped",
            "account_mapping_updated_at": "",
        }
        self.assertEqual(
            validated["roblox_packages"],
            [
                {
                    "package": "com.roblox.client",
                    "app_name": "",
                    "account_username": "",
                    "private_server_url": "",
                    "low_graphics_enabled": True,
                    "auto_reopen_enabled": True,
                    "auto_reconnect_enabled": True,
                    "enabled": True,
                    "username_source": "not_set",
                    "roblox_user_id": 0,
                    "roblox_cookie": "",
                    "expected_place_id": 0,
                    "expected_root_place_id": 0,
                    "expected_universe_id": 0,
                    **_mapping_defaults,
                },
                {
                    "package": "com.roblox.client.clone1",
                    "app_name": "",
                    "account_username": "",
                    "private_server_url": "",
                    "low_graphics_enabled": True,
                    "auto_reopen_enabled": True,
                    "auto_reconnect_enabled": True,
                    "enabled": True,
                    "username_source": "not_set",
                    "roblox_user_id": 0,
                    "roblox_cookie": "",
                    "expected_place_id": 0,
                    "expected_root_place_id": 0,
                    "expected_universe_id": 0,
                    **_mapping_defaults,
                },
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

    def test_old_launch_action_key_is_removed_safely(self):
        cfg = default_config()
        old_key = "post" + "_launch_action"
        cfg[old_key] = "script_injection"
        validated = validate_config(cfg)
        self.assertNotIn(old_key, validated)

    def test_auto_execute_scripts_are_removed_safely(self):
        cfg = default_config()
        cfg["auto_execute_scripts"] = [
            "",
            "  loadstring(game:HttpGet(\"https://example.com/a.lua\"))()  ",
            "loadstring(game:HttpGet(\"https://example.com/a.lua\"))()",
            "print('second')",
        ]
        cfg["saved_scripts"] = ["print('hidden')"]
        cfg["post_launch_action"] = "script"
        validated = validate_config(cfg)
        self.assertNotIn("auto_execute_scripts", validated)
        self.assertNotIn("saved_scripts", validated)
        self.assertNotIn("post_launch_action", validated)

    def test_roblosecurity_cookie_is_normalized_and_masked(self):
        from agent.config import safe_config_view

        cfg = default_config()
        cfg["roblox_packages"] = [
            {
                "package": "com.roblox.client",
                "account_username": "Main",
                "enabled": True,
                "roblox_cookie": ".ROBLOSECURITY=_|WARNING:-DO-NOT-SHARE-THIS.TESTCOOKIE",
                "expected_place_id": "123",
                "expected_root_place_id": "456",
                "expected_universe_id": "789",
            }
        ]
        validated = validate_config(cfg)
        pkg = validated["roblox_packages"][0]
        self.assertEqual(pkg["roblox_cookie"], "_|WARNING:-DO-NOT-SHARE-THIS.TESTCOOKIE")
        self.assertEqual(pkg["expected_place_id"], 123)
        self.assertEqual(pkg["expected_root_place_id"], 456)
        self.assertEqual(pkg["expected_universe_id"], 789)
        safe = safe_config_view(validated)
        self.assertNotIn("TESTCOOKIE", safe["roblox_packages"][0]["roblox_cookie"])

    def test_launch_url_still_promotes_to_private_server_url(self):
        cfg = default_config()
        cfg["launch_mode"] = "web_url"
        cfg["launch_url"] = "https://www.roblox.com/share?code=ABC123&type=Server"
        validated = validate_config(cfg)
        self.assertEqual(validated["private_url_mode"], "global")
        self.assertEqual(validated["private_server_url"], cfg["launch_url"])

    def test_default_screen_mode_is_auto(self):
        cfg = default_config()
        validated = validate_config(cfg)
        self.assertEqual(validated["screen_mode"], "auto")

    def test_legacy_forced_landscape_migrates_to_auto_once(self):
        cfg = default_config()
        cfg["screen_mode"] = "landscape"
        validated = validate_config(cfg)
        self.assertEqual(validated["screen_mode"], "auto")
        self.assertTrue(validated["screen_mode_auto_migrated_v1"])

        cfg2 = dict(validated)
        cfg2["screen_mode"] = "landscape"
        validated2 = validate_config(cfg2)
        self.assertEqual(validated2["screen_mode"], "landscape")

    def test_portrait_config_is_preserved(self):
        cfg = default_config()
        cfg["screen_mode"] = "portrait"
        cfg["screen_mode_auto_migrated_v1"] = True
        validated = validate_config(cfg)
        self.assertEqual(validated["screen_mode"], "portrait")

    def test_portrait_density_guard_defaults_without_applying_density(self):
        cfg = default_config()
        validated = validate_config(cfg)
        self.assertTrue(validated["portrait_auto_density_fix"])
        self.assertEqual(validated["portrait_previous_density"], "")

    def test_screen_mode_allowed_values(self):
        for value, expected in (
            ("landscape", "landscape"),
            ("portrait", "portrait"),
            ("auto", "auto"),
            ("potrait", "portrait"),
        ):
            cfg = default_config()
            cfg["screen_mode"] = value
            if value in ("landscape", "portrait"):
                cfg["screen_mode_auto_migrated_v1"] = True
            validated = validate_config(cfg)
            self.assertEqual(validated["screen_mode"], expected, msg=value)

    def test_private_url_global_mode_uses_global_url_and_ignores_package_url(self):
        cfg = default_config()
        cfg["private_url_mode"] = "global"
        cfg["private_server_url"] = "https://www.roblox.com/share?code=GLOBAL&type=Server"
        entry = dict(cfg["roblox_packages"][0])
        entry["private_server_url"] = "https://www.roblox.com/share?code=PACKAGE&type=Server"
        validated = validate_config(cfg)
        self.assertIn("GLOBAL", effective_private_server_url(entry, validated))
        ctx = private_url_launch_context(entry, validated)
        self.assertEqual(ctx["private_url_mode"], "global")
        self.assertEqual(ctx["url_config_source"], "global")

    def test_private_url_separate_mode_uses_package_url_only(self):
        cfg = default_config()
        cfg["private_url_mode"] = "separate"
        cfg["private_server_url"] = "https://www.roblox.com/share?code=GLOBAL&type=Server"
        cfg["roblox_packages"][0]["private_server_url"] = "https://www.roblox.com/share?code=PACKAGE&type=Server"
        validated = validate_config(cfg)
        entry = validated["roblox_packages"][0]
        self.assertIn("PACKAGE", effective_private_server_url(entry, validated))
        self.assertNotIn("GLOBAL", effective_private_server_url(entry, validated))
        ctx = private_url_launch_context(entry, validated)
        self.assertEqual(ctx["private_url_mode"], "separate")
        self.assertEqual(ctx["url_config_source"], "package_specific")

    def test_private_url_separate_blank_package_is_app_only(self):
        cfg = default_config()
        cfg["private_url_mode"] = "separate"
        cfg["private_server_url"] = "https://www.roblox.com/share?code=GLOBAL&type=Server"
        cfg["roblox_packages"][0]["private_server_url"] = ""
        validated = validate_config(cfg)
        entry = validated["roblox_packages"][0]
        self.assertEqual(effective_private_server_url(entry, validated), "")
        ctx = private_url_launch_context(entry, validated)
        self.assertEqual(ctx["url_mode"], "app_only")
        self.assertEqual(ctx["url_config_source"], "blank")

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

    def test_public_profile_enables_remote_license_server(self):
        cfg = validate_config(default_config())
        self.assertEqual(cfg["install_profile"], "public")
        self.assertTrue(cfg["license"]["enabled"])
        self.assertEqual(cfg["license"]["mode"], "remote")
        self.assertIn("rejoin.deng", cfg["license"]["server_url"])

    def test_public_old_disabled_license_migrates_to_remote_enabled(self):
        cfg = default_config()
        cfg["license"] = {
            "enabled": False,
            "mode": "local",
            "key": "",
            "server_url": "",
            "install_id": "",
            "device_label": "",
            "channel": "stable",
            "last_status": "not_configured",
            "last_check_at": None,
            "disabled_by_user": False,
        }
        v = validate_config(cfg)
        self.assertTrue(v["license"]["enabled"])
        self.assertEqual(v["license"]["mode"], "remote")
        self.assertTrue(v["license"]["server_url"])

    def test_disabled_by_user_skips_public_force_enable(self):
        cfg = default_config()
        cfg["license"]["disabled_by_user"] = True
        cfg["license"]["enabled"] = False
        v = validate_config(cfg)
        self.assertFalse(v["license"]["enabled"])

        cfg = default_config()
        cfg["package_detection_hints"] = ["com.moons.*", "Roblox", "bad;rm"]
        with self.assertRaises(ConfigError):
            validate_config(cfg)
        self.assertEqual(normalize_package_detection_hint("com.moons.*"), "com.moons.")


if __name__ == "__main__":
    unittest.main()
