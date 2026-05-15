"""Package detection, URL precedence, cache, graphics, and supervisor recovery tests."""

import threading
import unittest
import unittest.mock

from agent import android
from agent.config import default_config, effective_private_server_url, validate_config
from agent.launcher import launch_package_structured, perform_rejoin
from agent.url_utils import mask_launch_url
from agent.supervisor import MultiPackageSupervisor, _PackageWorker


class PackageDetectionTests(unittest.TestCase):
    def test_detects_official_roblox_package(self):
        packages = ["com.other.app", "com.roblox.client", "net.random.launcher"]
        with unittest.mock.patch("agent.android.list_packages", return_value=packages):
            found = android.find_roblox_packages(["roblox"])
        self.assertIn("com.roblox.client", found)

    def test_detects_clone_like_package_via_hints_not_hardcoding_default_clone(self):
        packages = ["com.other.app", "com.example.bloxgame.lite", "com.roblox.client"]
        with unittest.mock.patch("agent.android.list_packages", return_value=packages):
            found = android.find_roblox_packages(["blox", "lite"])
        self.assertIn("com.example.bloxgame.lite", found)
        self.assertNotIn("com.other.app", found)

    def test_default_hints_do_not_hardcode_single_vendor_package(self):
        """Hints are fragments; default tuple must not encode one third-party id as sole signal."""
        hints = android._safe_detection_hints(None)
        self.assertIn("roblox", hints)
        self.assertTrue(all("com." not in h for h in hints))


class PrivateUrlTests(unittest.TestCase):
    def test_package_url_overrides_global(self):
        cfg = validate_config(default_config())
        cfg["private_server_url"] = "https://www.roblox.com/games/1/x?privateServerLinkCode=global"
        entry = dict(cfg["roblox_packages"][0])
        entry["private_server_url"] = "https://www.roblox.com/games/1/x?privateServerLinkCode=secretpriv"
        cfg = validate_config(cfg)
        eff = effective_private_server_url(entry, cfg)
        self.assertIn("secretpriv", eff)
        self.assertNotIn("global", eff)

    def test_private_url_masked_for_display(self):
        url = "https://www.roblox.com/games/1/x?privateServerLinkCode=abc123"
        masked = mask_launch_url(url) or ""
        self.assertNotIn("abc123", masked)
        self.assertIn("masked", masked.lower())


class SafeCacheTests(unittest.TestCase):
    def test_clear_safe_cache_targets_not_session_paths(self):
        with open(android.__file__, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("files/tmp", text)
        self.assertNotIn("shared_prefs", text)
        self.assertNotIn("cookies", text.lower())

    def test_apply_low_graphics_skips_without_root(self):
        with unittest.mock.patch("agent.android.detect_root", return_value=android.RootInfo(False, None, "")):
            self.assertEqual(android.apply_low_graphics_optimization("com.roblox.client"), "Skipped")


class LaunchStructuredTests(unittest.TestCase):
    def test_structured_launch_uses_launch_package_with_options(self):
        cfg = validate_config(default_config())
        entry = dict(cfg["roblox_packages"][0])
        with unittest.mock.patch.object(android, "package_installed", return_value=True), \
             unittest.mock.patch.object(android, "launch_package_with_options") as m:
            m.return_value = (android.CommandResult(("am",), 0, "ok", ""), "am_or_resolve")
            d = launch_package_structured(cfg, entry).as_dict()
        self.assertTrue(d["success"])
        self.assertEqual(d["method"], "am_or_resolve")

    def test_single_package_entry_passed_to_perform_rejoin(self):
        cfg = validate_config(default_config())
        entry = dict(cfg["roblox_packages"][0])
        with unittest.mock.patch.object(android, "package_installed", return_value=True), \
             unittest.mock.patch.object(android, "launch_package_with_options") as m, \
             unittest.mock.patch.object(android, "detect_root") as dr:
            dr.return_value = android.RootInfo(False, None, "")
            m.return_value = (android.CommandResult(("am",), 0, "ok", ""), "am_or_resolve")
            r = perform_rejoin({**cfg, "roblox_package": entry["package"]}, package_entry=entry, no_force_stop=True)
        self.assertTrue(r.success)


class SupervisorRecoveryTests(unittest.TestCase):
    def test_restart_budget_blocks_spam(self):
        w = _PackageWorker(
            {"package": "com.roblox.client", "auto_reopen_enabled": True, "auto_reconnect_enabled": True},
            {"auto_rejoin_enabled": True, "supervisor": {"max_restart_attempts_per_hour": 2, "enabled": True}},
            {"com.roblox.client": "Offline"},
            threading.Event(),
        )
        self.assertTrue(w._restart_budget_ok())
        w._record_restart()
        w._record_restart()
        self.assertFalse(w._restart_budget_ok())

    def test_multi_supervisor_accepts_entries_not_only_strings(self):
        cfg = validate_config(default_config())
        entries = [dict(cfg["roblox_packages"][0])]
        sup = MultiPackageSupervisor(entries, cfg)
        self.assertEqual(sup.packages, ["com.roblox.client"])


if __name__ == "__main__":
    unittest.main()
