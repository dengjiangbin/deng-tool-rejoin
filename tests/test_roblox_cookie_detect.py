import unittest
from unittest.mock import patch

from agent import roblox_cookie_detect as rcd


class RobloxCookieDetectTests(unittest.TestCase):
    def test_cookie_from_pref_xml_reads_roblosecurity_key(self) -> None:
        xml = (
            '<?xml version="1.0" encoding="utf-8" standalone="yes" ?>'
            '<map>'
            '<string name=".ROBLOSECURITY">_|WARNING:-DO-NOT-SHARE-THIS.ABCDEF1234567890</string>'
            "</map>"
        )
        self.assertEqual(
            rcd.cookie_from_pref_xml(xml),
            "_|WARNING:-DO-NOT-SHARE-THIS.ABCDEF1234567890",
        )

    def test_cookie_from_pref_xml_strips_prefix(self) -> None:
        xml = (
            '<?xml version="1.0" encoding="utf-8" standalone="yes" ?>'
            '<map>'
            '<string name="cookie">.ROBLOSECURITY=_|WARNING:-DO-NOT-SHARE-THIS.SECONDVALUE</string>'
            "</map>"
        )
        self.assertEqual(
            rcd.cookie_from_pref_xml(xml),
            "_|WARNING:-DO-NOT-SHARE-THIS.SECONDVALUE",
        )

    def test_detect_roblox_cookie_uses_existing_config_value(self) -> None:
        entry = {"roblox_cookie": "_|WARNING:-DO-NOT-SHARE-THIS.CONFIGCOOKIE"}
        cookie = rcd.detect_roblox_cookie("com.roblox.client", entry=entry, use_root=False)
        self.assertEqual(cookie, "_|WARNING:-DO-NOT-SHARE-THIS.CONFIGCOOKIE")

    def test_detect_roblox_cookie_scans_shared_prefs_via_root(self) -> None:
        xml = (
            '<?xml version="1.0" encoding="utf-8" standalone="yes" ?>'
            '<map>'
            '<string name=".ROBLOSECURITY">_|WARNING:-DO-NOT-SHARE-THIS.ROOTFOUND</string>'
            "</map>"
        )
        with patch("agent.roblox_cookie_detect.root_access.has_root", return_value=True), \
             patch("agent.roblox_cookie_detect.root_access.list_root_glob", return_value=["/data/data/com.roblox.client/shared_prefs/auth.xml"]), \
             patch("agent.roblox_cookie_detect.root_access.read_root_file", return_value=xml), \
             patch("agent.roblox_cookie_detect._root_scan_webview_cookies", return_value=""):
            cookie = rcd.detect_roblox_cookie("com.roblox.client", use_root=True)
        self.assertEqual(cookie, "_|WARNING:-DO-NOT-SHARE-THIS.ROOTFOUND")

    def test_detect_roblox_cookie_falls_back_to_webview_db(self) -> None:
        with patch("agent.roblox_cookie_detect.root_access.has_root", return_value=True), \
             patch("agent.roblox_cookie_detect._root_scan_shared_prefs", return_value=""), \
             patch(
                 "agent.roblox_cookie_detect._root_scan_webview_cookies",
                 return_value="_|WARNING:-DO-NOT-SHARE-THIS.WEBVIEW",
             ):
            cookie = rcd.detect_roblox_cookie("com.roblox.client", use_root=True)
        self.assertEqual(cookie, "_|WARNING:-DO-NOT-SHARE-THIS.WEBVIEW")


if __name__ == "__main__":
    unittest.main()
