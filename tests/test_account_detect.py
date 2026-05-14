import unittest

from agent.account_detect import is_safe_username_value, username_from_pref_xml


class AccountDetectTests(unittest.TestCase):
    def test_extracts_only_allowlisted_username_key(self):
        xml = """<?xml version="1.0" encoding="utf-8"?>
<map>
  <string name="display_name">deng1629</string>
  <string name="theme">dark</string>
</map>
"""
        self.assertEqual(username_from_pref_xml(xml), "deng1629")

    def test_ignores_forbidden_secret_keys(self):
        xml = """<?xml version="1.0" encoding="utf-8"?>
<map>
  <string name="session_token">abcdef1234567890abcdef1234567890abcdef</string>
  <string name="cookie">.ROBLOSECURITY=secret</string>
  <string name="password">nope</string>
</map>
"""
        self.assertIsNone(username_from_pref_xml(xml))

    def test_ignores_token_like_values_even_on_safe_keys(self):
        xml = """<?xml version="1.0" encoding="utf-8"?>
<map>
  <string name="username">abcdefghijklmnopqrstuvwxyz1234567890</string>
</map>
"""
        self.assertIsNone(username_from_pref_xml(xml))

    def test_safe_username_value_rules(self):
        self.assertTrue(is_safe_username_value("AltAccount1"))
        self.assertFalse(is_safe_username_value("abc/def"))
        self.assertFalse(is_safe_username_value("token_abcdefghijklmnopqrstuvwxyz123456"))


if __name__ == "__main__":
    unittest.main()
