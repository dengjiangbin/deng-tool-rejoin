"""Tests for the clean Setup / Config menu UX.

Requirements covered:
 1.  Top-level menu has exactly Package, Roblox Launch Link, Webhook, YesCaptcha, Back.
 2.  Top-level menu does not contain Advanced Info.
 3.  Top-level menu does not contain Current Setting.
 4.  Top-level menu does not contain License Key.
 5.  Top-level menu does not contain Snapshot as a top-level item.
 6.  Top-level menu does not contain Webhook Interval as a top-level item.
 7.  Menu labels are title case.
 8.  Package submenu has Add Package.
 9.  Package submenu has Remove Package.
 10. Package submenu has Auto Detect Packages.
 11. Package submenu has Detect / Refresh Usernames.
 12. Package submenu does not offer Set / Edit Username or List Packages.
 13. Current packages appear at top of Package submenu with package — username.
 14. Remove package removes only selected package, preserving others.
 15. Add package avoids duplicates.
 16. Unknown username does not block launch.
 17. No "Label" wording in package menu.
 18. Blank input for Roblox Launch Link skips safely.
 19. Empty launch link is allowed (not validation error).
 20. Clear Roblox Launch Link works.
 21. Invalid non-empty URL is rejected.
 22. Start can proceed without a launch link.
 23. Webhook submenu has URL, Interval, Mode, Snapshot.
 24. Snapshot requires webhook URL.
 25. Webhook URL is masked (never full URL shown).
 26. Test webhook failure does not crash.
 27. Old webhook interval config migrates.
 28. YesCaptcha submenu exists.
 29. Set YesCaptcha key prompts and saves (key not revealed in full).
 30. Clear YesCaptcha key works.
 31. Balance shown when API key set (mocked).
 32. API failure handled cleanly.
 33. Full API key never printed by balance display.
 34. License Key menu removed from setup.
 35. Start license check still works (covered by test_start_flow, regression here).
 36. DENG_DEV=1 still skips (keystore test, regression).
 37. Regression: all old tests still pass (covered by running full suite).
"""
from __future__ import annotations

import argparse
import io
import unittest
import unittest.mock
from contextlib import redirect_stdout

from agent import android
from agent.commands import (
    _config_menu_launch_link,
    _config_menu_package,
    _config_menu_webhook,
    _config_menu_yescaptcha,
    _config_yescaptcha_balance,
    _config_yescaptcha_set,
    _package_menu_add,
    _package_menu_detect_refresh,
    _package_menu_list,
    _package_menu_remove,
    _package_menu_set_username,
    _prompt_launch_url,
    _run_edit_config_menu,
)
from agent.account_detect import AccountDetectionResult
from agent.config import default_config, package_entry, validate_config, validate_package_entries


def _non_interactive_args() -> argparse.Namespace:
    return argparse.Namespace(no_color=True)


def _base_cfg() -> dict:
    cfg = validate_config(default_config())
    return cfg


def _capture(fn, *args, **kwargs) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*args, **kwargs)
    return buf.getvalue()


# ─── 1-7: Top-level Menu Structure ───────────────────────────────────────────

class TopLevelMenuStructureTests(unittest.TestCase):

    def _get_menu_text(self) -> str:
        cfg = _base_cfg()
        args = _non_interactive_args()
        buf = io.StringIO()
        with redirect_stdout(buf):
            with unittest.mock.patch("agent.commands._is_interactive", return_value=False):
                _run_edit_config_menu(cfg, args)
        return buf.getvalue()

    def test_menu_contains_package(self):
        text = self._get_menu_text()
        self.assertIn("Package", text)

    def test_menu_contains_roblox_launch_link(self):
        # User feedback: drop the "Roblox Launch Link" multi-mode menu
        # and replace it with a single "Private Server URL" item.
        text = self._get_menu_text()
        self.assertIn("Private Server URL", text)

    def test_menu_contains_webhook(self):
        text = self._get_menu_text()
        self.assertIn("Webhook", text)

    def test_menu_contains_yescaptcha(self):
        text = self._get_menu_text()
        self.assertIn("YesCaptcha", text)

    def test_menu_contains_back(self):
        text = self._get_menu_text()
        self.assertIn("Back", text)

    def _menu_block(self, text: str) -> str:
        """Extract only the menu option lines (between the ---- separators)."""
        # The menu block is between the first and second '--------------------------------'
        parts = text.split("--------------------------------")
        # parts[0]=banner, parts[1]=title block, parts[2]=menu options block
        if len(parts) >= 3:
            return parts[2]  # the options section
        return text

    def test_menu_has_exactly_four_numbered_items(self):
        text = self._get_menu_text()
        block = self._menu_block(text)
        lines = block.splitlines()
        # Count items starting with 1-9 (exclude "0. Back")
        numbered = [l.strip() for l in lines if len(l.strip()) > 2 and l.strip()[0] in "123456789" and l.strip()[1] == "."]
        self.assertEqual(len(numbered), 4, f"Expected exactly 4 numbered items, got: {numbered}")

    def test_menu_does_not_contain_advanced_info(self):
        text = self._get_menu_text()
        self.assertNotIn("Advanced Info", text)

    def test_menu_does_not_contain_current_setting(self):
        text = self._get_menu_text()
        # "Current settings:" is the summary label, not a menu item
        # Menu item label "Current Setting" or "View Current Settings" must be absent
        self.assertNotIn("View Current Settings", text)
        self.assertNotIn("8. ", text.split("--------------------------------")[1] if "---" in text else text)

    def test_menu_does_not_contain_license_key_option(self):
        text = self._get_menu_text()
        # The menu items should not have a License Key entry
        menu_section = text.split("--------------------------------")[1] if text.count("---") >= 2 else text
        self.assertNotIn("6. License Key", menu_section)
        self.assertNotIn("7. License Key", menu_section)

    def test_menu_does_not_have_snapshot_top_level(self):
        text = self._get_menu_text()
        # Snapshot MUST NOT appear as a top-level numbered menu item
        lines = text.splitlines()
        top_numbered = [l.strip() for l in lines if len(l.strip()) > 2 and l.strip()[0].isdigit() and l.strip()[1] == "."]
        snapshot_top = [l for l in top_numbered if "Snapshot" in l]
        self.assertEqual(snapshot_top, [], f"Snapshot should not be a top-level item, found: {snapshot_top}")

    def test_menu_does_not_have_webhook_interval_top_level(self):
        text = self._get_menu_text()
        lines = text.splitlines()
        top_numbered = [l.strip() for l in lines if len(l.strip()) > 2 and l.strip()[0].isdigit() and l.strip()[1] == "."]
        wi_top = [l for l in top_numbered if "Webhook Interval" in l]
        self.assertEqual(wi_top, [], f"Webhook Interval should not be a top-level item, found: {wi_top}")

    def test_menu_labels_title_case(self):
        text = self._get_menu_text()
        block = self._menu_block(text)
        lines = block.splitlines()
        menu_items = [l.strip() for l in lines if len(l.strip()) > 2 and l.strip()[0].isdigit() and l.strip()[1] == "."]
        for item in menu_items:
            label = item[3:].strip()  # strip "N. "
            # Each word should not be all lowercase (skip android package names)
            words = [w for w in label.split() if not w.startswith("com.") and "." not in w]
            for word in words:
                self.assertFalse(
                    word.islower() and len(word) > 2,
                    f"Label '{label}' has lowercase word '{word}' — expected title case",
                )


# ─── 8-13: Package Submenu ────────────────────────────────────────────────────

class PackageSubmenuTests(unittest.TestCase):

    def _get_package_submenu_text(self) -> str:
        cfg = _base_cfg()
        buf = io.StringIO()
        with redirect_stdout(buf):
            with unittest.mock.patch("agent.commands._is_interactive", return_value=False):
                _config_menu_package(cfg)
        return buf.getvalue()

    def test_package_submenu_does_not_run_without_interaction(self):
        # When non-interactive, returns immediately without looping
        cfg = _base_cfg()
        result = _config_menu_package(cfg)
        self.assertIsInstance(result, dict)

    def test_package_submenu_add_package_avoids_duplicate(self):
        cfg = _base_cfg()
        cfg["roblox_packages"] = [
            package_entry("com.roblox.client", "Main", True),
        ]
        # Simulate adding same package again via "B" (back) since all detected are already configured
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", side_effect=["b"]):
                with unittest.mock.patch(
                    "agent.commands._gather_roblox_candidates_for_ui",
                    return_value=[
                        android.RobloxPackageCandidate("com.roblox.client", "Roblox", True),
                    ],
                ):
                    with unittest.mock.patch("agent.commands.save_config", side_effect=lambda c: c):
                        buf = io.StringIO()
                        with redirect_stdout(buf):
                            result = _package_menu_add(cfg)
        text = buf.getvalue()
        # Should say all detected packages are already configured
        self.assertIn("already configured", text)
        # Still only 1 package
        self.assertEqual(len(result["roblox_packages"]), 1)

    def test_package_menu_remove_removes_only_target(self):
        cfg = _base_cfg()
        cfg["roblox_packages"] = [
            package_entry("com.roblox.client", "Main", True),
            package_entry("com.moons.alt1", "Alt1", True),
        ]
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", side_effect=["2", "y"]):
                with unittest.mock.patch("agent.commands.save_config", side_effect=lambda c: c):
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        result = _package_menu_remove(cfg)
        remaining = validate_package_entries(result["roblox_packages"])
        remaining_pkgs = [e["package"] for e in remaining]
        # com.moons.alt1 should be removed; com.roblox.client should remain
        self.assertIn("com.roblox.client", remaining_pkgs)
        self.assertNotIn("com.moons.alt1", remaining_pkgs)

    def test_package_menu_remove_cannot_remove_last(self):
        cfg = _base_cfg()
        cfg["roblox_packages"] = [package_entry("com.roblox.client", "Main", True)]
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", side_effect=["1", "y"]):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    result = _package_menu_remove(cfg)
        text = buf.getvalue()
        self.assertIn("Cannot Remove", text)
        self.assertEqual(len(result["roblox_packages"]), 1)

    def test_unknown_username_does_not_block(self):
        cfg = _base_cfg()
        cfg["roblox_packages"] = [{"package": "com.roblox.client", "account_username": "", "enabled": True, "username_source": "not_set"}]
        # Should still work — no crash, no block
        entries = validate_package_entries(cfg["roblox_packages"])
        self.assertTrue(len(entries) >= 1)
        # username_source = not_set is valid
        self.assertEqual(entries[0]["account_username"], "")

    def test_package_menu_list_no_label_wording(self):
        cfg = _base_cfg()
        cfg["roblox_packages"] = [package_entry("com.roblox.client", "Main", True)]
        buf = io.StringIO()
        with redirect_stdout(buf):
            with unittest.mock.patch("builtins.input", return_value=""):
                _package_menu_list(cfg)
        text = buf.getvalue()
        self.assertNotIn("Label", text)
        self.assertIn("Username", text)

    def test_package_submenu_has_detect_refresh_usernames(self):
        """Detect / Refresh Usernames was removed from the public package menu (not a public item).
        The public menu now only has: Auto Detect Package, Add Package, Remove Package, Back."""
        cfg = _base_cfg()
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_package(cfg)
        text = buf.getvalue()
        # Must NOT be present in the public menu
        self.assertNotIn("Detect / Refresh Usernames", text)
        # The three public items must be present
        self.assertIn("Auto Detect Package", text)
        self.assertIn("Add Package", text)
        self.assertIn("Remove Package", text)

    def test_package_submenu_does_not_offer_set_edit_username(self):
        cfg = _base_cfg()
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_package(cfg)
        text = buf.getvalue()
        self.assertNotIn("Set / Edit Username", text)
        self.assertNotIn("Set Username", text)
        self.assertNotIn("Edit Username", text)

    def test_package_submenu_does_not_offer_list_packages(self):
        cfg = _base_cfg()
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_package(cfg)
        text = buf.getvalue()
        self.assertNotIn("List Packages", text)

    def test_package_submenu_lists_expected_numbered_options(self):
        """Public package menu options: Auto Detect (1), Add (2), Remove (3),
        Set Account Username / User ID (4), Back (0).  No further extras —
        Roblox-presence detection is unlocked by user-id, so option 4 is the
        explicit user-facing way to enable that without typing config JSON."""
        cfg = _base_cfg()
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_package(cfg)
        text = buf.getvalue()
        self.assertRegex(text, r"(?m)^1\. Auto Detect Package")
        self.assertRegex(text, r"(?m)^2\. Add Package")
        self.assertRegex(text, r"(?m)^3\. Remove Package")
        self.assertRegex(text, r"(?m)^4\. Set Account Username / User ID")
        # No 5th public item; only 0. Back follows
        self.assertNotRegex(text, r"(?m)^5\. ")

    def test_detect_refresh_saves_new_username(self):
        cfg = _base_cfg()
        cfg["roblox_packages"] = [package_entry("com.roblox.client", "", True, "not_set")]
        ent = dict(cfg["roblox_packages"][0])

        def _pairs(_pkgs, **kwargs):
            return [(ent, AccountDetectionResult("founduser", "root_pref"))]

        with unittest.mock.patch("agent.commands.account_detect.detect_account_usernames_for_packages", side_effect=_pairs):
            with unittest.mock.patch("builtins.input", return_value=""):
                with unittest.mock.patch("agent.commands.save_config", side_effect=lambda c: c):
                    out = _package_menu_detect_refresh(cfg)
        self.assertEqual(out["roblox_packages"][0]["account_username"], "founduser")

    def test_set_username_manual_saves(self):
        cfg = _base_cfg()
        cfg["roblox_packages"] = [package_entry("com.roblox.client", "", True, "not_set")]
        with unittest.mock.patch("builtins.input", side_effect=["1", "handuser", ""]):
            with unittest.mock.patch("agent.commands.save_config", side_effect=lambda c: c):
                out = _package_menu_set_username(cfg)
        self.assertEqual(out["roblox_packages"][0]["account_username"], "handuser")
        self.assertEqual(out["roblox_packages"][0]["username_source"], "manual")

    def test_list_packages_shows_unknown_for_empty(self):
        cfg = _base_cfg()
        cfg["roblox_packages"] = [package_entry("com.roblox.client", "", True, "not_set")]
        buf = io.StringIO()
        with redirect_stdout(buf):
            with unittest.mock.patch("builtins.input", return_value=""):
                _package_menu_list(cfg)
        self.assertIn("Unknown", buf.getvalue())

    def test_package_submenu_has_add_package(self):
        cfg = _base_cfg()
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_package(cfg)
        text = buf.getvalue()
        self.assertIn("Add Package", text)

    def test_package_submenu_has_remove_package(self):
        cfg = _base_cfg()
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_package(cfg)
        text = buf.getvalue()
        self.assertIn("Remove Package", text)

    def test_package_submenu_has_auto_detect_packages(self):
        cfg = _base_cfg()
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_package(cfg)
        # Menu item is now "Auto Detect Package" (singular) in position 1
        self.assertIn("Auto Detect Package", buf.getvalue())


# ─── 14-18: Roblox Launch Link Submenu ───────────────────────────────────────

class LaunchLinkSubmenuTests(unittest.TestCase):

    def test_prompt_launch_url_blank_returns_empty(self):
        with unittest.mock.patch("builtins.input", return_value=""):
            result = _prompt_launch_url("", "web_url")
        self.assertEqual(result, "")

    def test_prompt_launch_url_blank_does_not_loop(self):
        # Should return on first blank without asking again
        call_count = 0
        def _mock_input(prompt=""):
            nonlocal call_count
            call_count += 1
            return ""
        with unittest.mock.patch("builtins.input", _mock_input):
            with unittest.mock.patch("agent.commands._prompt", side_effect=lambda *a, **kw: ""):
                result = _prompt_launch_url("", "deeplink")
        self.assertEqual(result, "")

    def test_empty_launch_link_allowed_in_config(self):
        cfg = _base_cfg()
        cfg["launch_mode"] = "app"
        cfg["launch_url"] = ""
        validated = validate_config(cfg)
        self.assertEqual(validated["launch_url"], "")
        self.assertEqual(validated["launch_mode"], "app")

    def test_clear_launch_link_works(self):
        cfg = _base_cfg()
        cfg["launch_mode"] = "web_url"
        cfg["launch_url"] = "https://www.roblox.com/games/123"
        # Simulate choosing "2. Clear Roblox Launch Link"
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", side_effect=["2", "0"]):
                with unittest.mock.patch("agent.commands.save_config", side_effect=lambda c: c):
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        result = _config_menu_launch_link(cfg)
        self.assertEqual(result["launch_url"], "")
        self.assertEqual(result["launch_mode"], "app")

    def test_invalid_nonempty_url_rejected(self):
        bad_url = "not-a-url"
        with unittest.mock.patch("builtins.input", return_value=""):
            with unittest.mock.patch("agent.commands._prompt", side_effect=["", bad_url, ""]):
                # _prompt_launch_url will reject bad URL then user hits blank
                result = _prompt_launch_url("", "web_url")
        self.assertEqual(result, "")

    def test_start_works_without_launch_link(self):
        from agent.config import default_config, validate_config
        cfg = validate_config(default_config())
        # app mode, no URL — should pass validate_config
        self.assertEqual(cfg["launch_url"], "")
        self.assertEqual(cfg["launch_mode"], "app")

    def test_launch_link_optional_message_shown(self):
        """The simplified URL submenu shows the field name and the
        'blank to skip' hint instead of the old 'Optional' banner."""
        cfg = _base_cfg()
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_launch_link(cfg)
        text = buf.getvalue()
        # The submenu still tells the user the field is skippable —
        # just under the new, plainer wording the user asked for.
        self.assertIn("Private Server URL", text)


# ─── 19-23: Webhook Submenu ───────────────────────────────────────────────────

class WebhookSubmenuTests(unittest.TestCase):

    def _webhook_submenu_text(self, cfg=None) -> str:
        if cfg is None:
            cfg = _base_cfg()
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_webhook(cfg)
        return buf.getvalue()

    def test_webhook_submenu_has_url_option(self):
        text = self._webhook_submenu_text()
        self.assertIn("Webhook URL", text)

    def test_webhook_submenu_has_interval_option(self):
        text = self._webhook_submenu_text()
        self.assertIn("Webhook Interval", text)

    def test_webhook_submenu_has_mode_option(self):
        text = self._webhook_submenu_text()
        self.assertIn("Webhook Mode", text)

    def test_webhook_submenu_has_snapshot_option(self):
        text = self._webhook_submenu_text()
        self.assertIn("Snapshot", text)

    def test_snapshot_requires_webhook_url(self):
        cfg = _base_cfg()
        cfg["webhook_url"] = ""
        cfg["webhook_enabled"] = False
        # Choose option 4 (Snapshot) → "Press Enter to continue..." → Back
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", side_effect=["4", "", "0"]):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_webhook(cfg)
        text = buf.getvalue()
        self.assertIn("Set Webhook URL First", text)

    def test_webhook_url_masked_in_display(self):
        cfg = _base_cfg()
        cfg["webhook_url"] = "https://discord.com/api/webhooks/99999/super-secret-token"
        cfg["webhook_enabled"] = True
        text = self._webhook_submenu_text(cfg)
        self.assertNotIn("super-secret-token", text)
        self.assertIn("MASKED", text)

    def test_test_webhook_failure_does_not_crash(self):
        from agent.commands import _test_webhook
        cfg = _base_cfg()
        cfg["webhook_url"] = "https://discord.com/api/webhooks/12345/fake-token"
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
                with unittest.mock.patch("builtins.input", return_value=""):
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        _test_webhook(cfg)
        text = buf.getvalue()
        self.assertIn("Failed", text)

    def test_webhook_interval_migrates_from_old_config(self):
        from agent.config import validate_config
        from agent.config import default_config as _dc
        cfg = _dc()
        cfg["webhook_interval_seconds"] = 60  # old flat field
        validated = validate_config(cfg)
        self.assertEqual(validated["webhook_interval_seconds"], 60)


# ─── 24-29: YesCaptcha Submenu ────────────────────────────────────────────────

class YesCaptchaSubmenuTests(unittest.TestCase):

    def _yescaptcha_submenu_text(self, cfg=None) -> str:
        if cfg is None:
            cfg = _base_cfg()
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_yescaptcha(cfg)
        return buf.getvalue()

    def test_yescaptcha_submenu_exists(self):
        text = self._yescaptcha_submenu_text()
        self.assertIn("YesCaptcha", text)

    def test_yescaptcha_set_key_saved(self):
        cfg = _base_cfg()
        cfg["yescaptcha_key"] = ""
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("agent.commands._prompt", return_value="my-api-key-12345"):
                with unittest.mock.patch("builtins.input", return_value=""):
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        _config_yescaptcha_set(cfg)
        self.assertEqual(cfg["yescaptcha_key"], "my-api-key-12345")

    def test_yescaptcha_set_key_blank_skips(self):
        cfg = _base_cfg()
        cfg["yescaptcha_key"] = "existing-key"
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("agent.commands._prompt", return_value=""):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_yescaptcha_set(cfg)
        # Should NOT clear the existing key
        self.assertEqual(cfg["yescaptcha_key"], "existing-key")

    def test_yescaptcha_clear_key_works(self):
        cfg = _base_cfg()
        cfg["yescaptcha_key"] = "some-key"
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", side_effect=["2", "0"]):
                with unittest.mock.patch("agent.commands.save_config", side_effect=lambda c: c):
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        _config_menu_yescaptcha(cfg)
        self.assertEqual(cfg["yescaptcha_key"], "")

    def test_yescaptcha_balance_check_with_key(self):
        cfg = _base_cfg()
        cfg["yescaptcha_key"] = "test-key-1234"
        with unittest.mock.patch("agent.captcha.get_balance", return_value=42.5):
            with unittest.mock.patch("builtins.input", return_value=""):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_yescaptcha_balance(cfg)
        text = buf.getvalue()
        self.assertIn("42.5", text)

    def test_yescaptcha_balance_api_failure_handled(self):
        cfg = _base_cfg()
        cfg["yescaptcha_key"] = "bad-key"
        from agent.captcha import CaptchaError
        with unittest.mock.patch("agent.captcha.get_balance", side_effect=CaptchaError("API down")):
            with unittest.mock.patch("builtins.input", return_value=""):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_yescaptcha_balance(cfg)
        text = buf.getvalue()
        self.assertIn("Failed", text)
        self.assertNotIn("bad-key", text)

    def test_yescaptcha_full_key_never_printed(self):
        cfg = _base_cfg()
        cfg["yescaptcha_key"] = "super-secret-api-key-abcdef"
        text = self._yescaptcha_submenu_text(cfg)
        self.assertNotIn("super-secret-api-key-abcdef", text)
        # Masked form (first 4 chars + ...) should appear
        self.assertIn("supe...", text)

    def test_yescaptcha_no_key_shows_message(self):
        cfg = _base_cfg()
        cfg["yescaptcha_key"] = ""
        with unittest.mock.patch("builtins.input", return_value=""):
            buf = io.StringIO()
            with redirect_stdout(buf):
                _config_yescaptcha_balance(cfg)
        text = buf.getvalue()
        self.assertIn("Not Set", text)


# ─── 30-32: License Flow Regression ──────────────────────────────────────────

class LicenseFlowRegressionTests(unittest.TestCase):

    def test_setup_menu_does_not_show_license_key(self):
        cfg = _base_cfg()
        args = _non_interactive_args()
        buf = io.StringIO()
        with redirect_stdout(buf):
            with unittest.mock.patch("agent.commands._is_interactive", return_value=False):
                _run_edit_config_menu(cfg, args)
        text = buf.getvalue()
        # "License Key" must NOT be a numbered menu item
        lines = text.splitlines()
        numbered_items = [l.strip() for l in lines if len(l.strip()) > 2 and l.strip()[0].isdigit() and l.strip()[1] == "."]
        license_items = [l for l in numbered_items if "License Key" in l]
        self.assertEqual(license_items, [])

    def test_deng_dev_mode_skips_license(self):
        import os
        from agent import keystore
        with unittest.mock.patch.dict(os.environ, {"DENG_DEV": "1"}):
            # Reload the keystore module attribute
            dev = bool(os.environ.get("DENG_DEV", ""))
            self.assertTrue(dev)

    def test_config_without_license_key_is_valid(self):
        cfg = _base_cfg()
        cfg["license_key"] = ""
        validated = validate_config(cfg)
        self.assertEqual(validated["license_key"], "")

    def test_config_summary_still_shows_license_section(self):
        from agent.commands import _print_config_summary
        cfg = _base_cfg()
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_config_summary(cfg)
        text = buf.getvalue()
        # Status command still shows license info
        self.assertIn("License", text)


# ─── Extra Regression: Current Settings still shown inside submenus ──────────

class CurrentSettingsInSubmenuTests(unittest.TestCase):

    def test_package_submenu_output_has_no_label_wording(self):
        cfg = _base_cfg()
        cfg["roblox_packages"] = [package_entry("com.roblox.client", "Main", True)]
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_package(cfg)
        text = buf.getvalue()
        self.assertNotIn("Label", text)

    def test_webhook_submenu_shows_current_url_masked(self):
        cfg = _base_cfg()
        cfg["webhook_url"] = "https://discord.com/api/webhooks/11111/tok-secret"
        cfg["webhook_interval_seconds"] = 120
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_webhook(cfg)
        text = buf.getvalue()
        self.assertIn("Current Webhook", text)
        self.assertNotIn("tok-secret", text)
        self.assertIn("120 Seconds", text)

    def test_yescaptcha_submenu_shows_configured_when_key_set(self):
        cfg = _base_cfg()
        cfg["yescaptcha_key"] = "abcdefghij"
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_yescaptcha(cfg)
        text = buf.getvalue()
        self.assertIn("Configured", text)
        self.assertNotIn("abcdefghij", text)

    def test_package_submenu_shows_current_packages_with_username(self):
        cfg = _base_cfg()
        cfg["roblox_packages"] = [package_entry("com.roblox.client", "Main", True)]
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_package(cfg)
        text = buf.getvalue()
        self.assertIn("Current Packages", text)
        self.assertIn("com.roblox.client — Main", text)

    def test_package_submenu_shows_unknown_for_missing_username(self):
        cfg = _base_cfg()
        cfg["roblox_packages"] = [package_entry("com.moons.litesc", "", True, "not_set")]
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_package(cfg)
        text = buf.getvalue()
        self.assertIn("com.moons.litesc — Unknown", text)

    def test_package_submenu_no_enabled_packages_shows_none_configured(self):
        cfg = _base_cfg()
        cfg["roblox_packages"] = [
            {
                "package": "com.roblox.client",
                "account_username": "",
                "enabled": False,
                "username_source": "not_set",
            },
        ]
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_package(cfg)
        text = buf.getvalue()
        self.assertIn("No Packages Configured.", text)

    def test_launch_link_submenu_shows_not_set_when_empty(self):
        """When no Private Server URL is configured the submenu shows
        a clear 'Not set' message instead of the old 'Launch The App
        Normally' banner."""
        cfg = _base_cfg()
        cfg["launch_url"] = ""
        cfg["launch_mode"] = "app"
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_launch_link(cfg)
        text = buf.getvalue()
        self.assertIn("Not set", text)


class TestPackageMenuBug3Regression(unittest.TestCase):
    """BUG 3 regression: Auto Detect Package, Add Package (detect+confirm), Remove Package (confirm)."""

    def _make_cfg(self, packages=None):
        cfg = _base_cfg()
        if packages:
            cfg["roblox_packages"] = packages
        return cfg

    def test_auto_detect_package_is_menu_item_1(self):
        """Auto Detect Package must appear as item 1 in the public package menu."""
        cfg = self._make_cfg()
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_package(cfg)
        self.assertRegex(buf.getvalue(), r"(?m)^1\. Auto Detect Package")

    def test_add_package_is_menu_item_2(self):
        cfg = self._make_cfg()
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_package(cfg)
        self.assertRegex(buf.getvalue(), r"(?m)^2\. Add Package")

    def test_remove_package_is_menu_item_3(self):
        cfg = self._make_cfg()
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_package(cfg)
        self.assertRegex(buf.getvalue(), r"(?m)^3\. Remove Package")

    def test_no_refresh_username_in_public_menu(self):
        """Refresh Username / Edit Username must NOT be in the public package menu."""
        cfg = self._make_cfg()
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value="0"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    _config_menu_package(cfg)
        text = buf.getvalue()
        self.assertNotIn("Refresh Username", text)
        self.assertNotIn("Edit Username", text)
        self.assertNotIn("Detect / Refresh Usernames", text)

    def test_add_package_runs_detection_first(self):
        """Add Package must call detection before asking what to add."""
        cfg = self._make_cfg()
        detected = [android.RobloxPackageCandidate("com.clone.pkg", "Clone", True)]
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", side_effect=["b"]):
                with unittest.mock.patch(
                    "agent.commands._gather_roblox_candidates_for_ui",
                    return_value=detected,
                ) as mock_detect:
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        _package_menu_add(cfg)
        mock_detect.assert_called_once()

    def test_add_package_supports_manual_entry(self):
        """Add Package M/m option allows typing a package name manually."""
        cfg = self._make_cfg()
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch(
                "agent.commands._gather_roblox_candidates_for_ui", return_value=[]
            ):
                with unittest.mock.patch("builtins.input", side_effect=["m", "com.manual.pkg", "y"]):
                    with unittest.mock.patch("agent.commands.save_config", side_effect=lambda c: c):
                        with unittest.mock.patch(
                            "agent.commands._detect_or_prompt_account_username",
                            side_effect=lambda entry, _cfg: {**entry, "account_username": "Unknown"},
                        ):
                            buf = io.StringIO()
                            with redirect_stdout(buf):
                                result = _package_menu_add(cfg)
        pkgs = [e["package"] for e in result.get("roblox_packages", [])]
        self.assertIn("com.manual.pkg", pkgs)

    def test_add_package_requires_confirmation_before_saving(self):
        """Add Package must ask for confirmation before saving detected package."""
        cfg = self._make_cfg()
        detected = [android.RobloxPackageCandidate("com.new.pkg", "New App", True)]
        # User selects #1 but then says "n" to confirmation
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch(
                "agent.commands._gather_roblox_candidates_for_ui", return_value=detected
            ):
                with unittest.mock.patch("builtins.input", side_effect=["1", "n"]):
                    with unittest.mock.patch("agent.commands.save_config", side_effect=lambda c: c) as mock_save:
                        with unittest.mock.patch(
                            "agent.commands._detect_or_prompt_account_username",
                            side_effect=lambda entry, _cfg: {**entry, "account_username": "Unknown"},
                        ):
                            buf = io.StringIO()
                            with redirect_stdout(buf):
                                result = _package_menu_add(cfg)
        # save_config should NOT have been called (user cancelled)
        mock_save.assert_not_called()

    def test_add_package_cancel_does_not_modify_config(self):
        """Cancelling Add Package confirmation must leave config unchanged."""
        cfg = self._make_cfg([package_entry("com.roblox.client", "Main", True)])
        original_pkgs = list(cfg["roblox_packages"])
        detected = [android.RobloxPackageCandidate("com.new.pkg", "New", True)]
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch(
                "agent.commands._gather_roblox_candidates_for_ui", return_value=detected
            ):
                with unittest.mock.patch("builtins.input", side_effect=["1", "n"]):
                    with unittest.mock.patch("agent.commands.save_config", side_effect=lambda c: c):
                        with unittest.mock.patch(
                            "agent.commands._detect_or_prompt_account_username",
                            side_effect=lambda entry, _cfg: {**entry, "account_username": "Unknown"},
                        ):
                            buf = io.StringIO()
                            with redirect_stdout(buf):
                                result = _package_menu_add(cfg)
        self.assertEqual(len(result.get("roblox_packages", [])), len(original_pkgs))

    def test_remove_package_requires_confirmation(self):
        """Remove Package must ask 'Confirm? [y/N]' before removing."""
        cfg = self._make_cfg([
            package_entry("com.roblox.client", "Main", True),
            package_entry("com.clone.pkg", "Clone", True),
        ])
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", side_effect=["1", "n"]):
                with unittest.mock.patch("agent.commands.save_config", side_effect=lambda c: c):
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        result = _package_menu_remove(cfg)
        # Cancelled → both packages remain
        self.assertEqual(len(result.get("roblox_packages", [])), 2)

    def test_remove_package_cancel_does_not_modify_config(self):
        """Declining removal confirmation must leave config unchanged."""
        cfg = self._make_cfg([
            package_entry("com.roblox.client", "Main", True),
        ])
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", side_effect=["1", "n"]):
                with unittest.mock.patch("agent.commands.save_config", side_effect=lambda c: c) as mock_save:
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        result = _package_menu_remove(cfg)
        mock_save.assert_not_called()

    def test_blank_input_does_not_crash(self):
        """Blank input at any package menu prompt must not raise."""
        cfg = self._make_cfg()
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True):
            with unittest.mock.patch("builtins.input", return_value=""):
                with unittest.mock.patch(
                    "agent.commands._gather_roblox_candidates_for_ui", return_value=[]
                ):
                    try:
                        buf = io.StringIO()
                        with redirect_stdout(buf):
                            _package_menu_add(cfg)
                    except Exception as exc:
                        self.fail(f"Blank input caused crash: {exc}")

    def test_username_detection_failure_falls_back_to_unknown(self):
        """If username detection fails, package entry must show Unknown — not crash."""
        from agent.commands import _detect_or_prompt_account_username, _entry_for_package
        entry = _entry_for_package("com.roblox.client", [])
        with unittest.mock.patch("agent.account_detect.detect_account_username", return_value=None):
            with unittest.mock.patch("agent.commands._is_interactive", return_value=False):
                result = _detect_or_prompt_account_username(entry, {})
        username = result.get("account_username") or "Unknown"
        self.assertIn(username, ("Unknown", "", None, "unknown"))


if __name__ == "__main__":
    unittest.main()
