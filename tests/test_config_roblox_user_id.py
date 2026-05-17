"""Per-package ``roblox_user_id`` config migration."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.config import (
    _validate_roblox_user_id,
    package_entry,
    validate_package_entries,
)


class ValidateRobloxUserIdTests(unittest.TestCase):
    def test_int_positive_kept(self) -> None:
        self.assertEqual(_validate_roblox_user_id(12345), 12345)

    def test_str_digit_positive_coerced(self) -> None:
        self.assertEqual(_validate_roblox_user_id("12345"), 12345)
        self.assertEqual(_validate_roblox_user_id("  9876  "), 9876)

    def test_empty_or_none_becomes_zero(self) -> None:
        self.assertEqual(_validate_roblox_user_id(None), 0)
        self.assertEqual(_validate_roblox_user_id(""), 0)

    def test_invalid_does_not_raise(self) -> None:
        # Old configs may have garbage in this field; we must not break Start.
        self.assertEqual(_validate_roblox_user_id("not-a-number"), 0)
        self.assertEqual(_validate_roblox_user_id(["x"]), 0)
        self.assertEqual(_validate_roblox_user_id({"a": 1}), 0)

    def test_negative_becomes_zero(self) -> None:
        self.assertEqual(_validate_roblox_user_id(-5), 0)
        self.assertEqual(_validate_roblox_user_id("-100"), 0)


class PackageEntryRoblowUserIdTests(unittest.TestCase):
    def test_default_user_id_is_zero(self) -> None:
        e = package_entry("com.roblox.client")
        self.assertEqual(e["roblox_user_id"], 0)

    def test_can_set_user_id(self) -> None:
        e = package_entry("com.roblox.client", roblox_user_id=1234567)
        self.assertEqual(e["roblox_user_id"], 1234567)

    def test_user_id_string_coerced(self) -> None:
        e = package_entry("com.roblox.client", roblox_user_id="9876")
        self.assertEqual(e["roblox_user_id"], 9876)


class ValidatePackageEntriesRobloxUserIdTests(unittest.TestCase):
    def test_old_config_without_user_id_loads(self) -> None:
        # Simulate a config dict that pre-dates the roblox_user_id field.
        legacy = [
            {
                "package": "com.roblox.client",
                "account_username": "Main",
                "enabled": True,
                "username_source": "manual",
            }
        ]
        entries = validate_package_entries(legacy)
        self.assertEqual(entries[0]["roblox_user_id"], 0)
        # Should not raise — old configs must keep working.

    def test_user_id_preserved_through_round_trip(self) -> None:
        legacy = [
            {
                "package": "com.roblox.client.clone1",
                "account_username": "Alt",
                "enabled": True,
                "username_source": "manual",
                "roblox_user_id": 42,
            }
        ]
        entries = validate_package_entries(legacy)
        self.assertEqual(entries[0]["roblox_user_id"], 42)

    def test_invalid_user_id_falls_back_to_zero(self) -> None:
        legacy = [
            {
                "package": "com.roblox.client",
                "account_username": "Main",
                "enabled": True,
                "username_source": "manual",
                "roblox_user_id": "garbage",
            }
        ]
        entries = validate_package_entries(legacy)
        # garbage → 0; Start can still run, presence falls back to local heur.
        self.assertEqual(entries[0]["roblox_user_id"], 0)


if __name__ == "__main__":
    unittest.main()
