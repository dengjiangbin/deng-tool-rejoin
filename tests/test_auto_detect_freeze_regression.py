"""Regression tests for probe p-c9207c8a08.

The previous fix added a per-package safe username detector that read
``prefs.xml`` via root.  Probe p-c9207c8a08 showed installed build
387c5cae55df with all 8 selected packages already having usernames cached
from a prior detection — but the user reported that opening the Packages
menu and adding new packages could freeze Termux on a fresh install.

These tests pin the rules:

* Opening the Packages submenu MUST NOT block on root or call any
  legacy mapping helpers, even when no package has a saved username.
* ``safe_detect_username_for_package`` must always return a string and
  return ``"Unknown"`` when root reads time out or fail.
* Auto Detect Package's bounded post-add username pass must respect a
  global deadline so it cannot stall the UI when there are many
  packages or root is slow.
* Manual Add Package never calls the legacy Refresh Mapping helpers and
  never asks the user for a username label.
"""

from __future__ import annotations

import io
import re
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from agent import package_username
from agent.commands import (
    _bounded_post_add_username_detection,
    _config_menu_package,
    _package_menu_add,
    _package_menu_auto_detect,
)
from agent.config import default_config, package_entry, validate_config


def _cfg(packages: list[dict] | None = None) -> dict:
    cfg = validate_config(default_config())
    if packages is not None:
        cfg["roblox_packages"] = packages
    return cfg


class SafeDetectUsernameTests(unittest.TestCase):
    def test_safe_detect_returns_username_when_pref_has_value(self) -> None:
        xml = (
            "<map><string name=\"username\">deng1629</string>"
            "<string name=\"displayName\">DENG</string></map>"
        )
        with mock.patch("agent.root_access.read_root_file", return_value=xml):
            self.assertEqual(
                package_username.safe_detect_username_for_package(
                    "com.moons.litesc"
                ),
                "deng1629",
            )

    def test_safe_detect_returns_unknown_on_missing_file(self) -> None:
        with mock.patch("agent.root_access.read_root_file", return_value=None):
            self.assertEqual(
                package_username.safe_detect_username_for_package(
                    "com.moons.litesc"
                ),
                "Unknown",
            )

    def test_safe_detect_returns_unknown_on_root_exception(self) -> None:
        def _boom(*_a, **_k):
            raise RuntimeError("simulated root failure")

        with mock.patch("agent.root_access.read_root_file", side_effect=_boom):
            self.assertEqual(
                package_username.safe_detect_username_for_package(
                    "com.moons.litesc"
                ),
                "Unknown",
            )

    def test_safe_detect_returns_unknown_on_blank_package(self) -> None:
        self.assertEqual(
            package_username.safe_detect_username_for_package(""),
            "Unknown",
        )

    def test_safe_detect_does_not_call_legacy_mapping_helpers(self) -> None:
        with mock.patch("agent.root_access.read_root_file", return_value=None), \
             mock.patch(
                 "agent.account_detect.detect_account_username",
                 side_effect=AssertionError("legacy username scan"),
             ), \
             mock.patch(
                 "agent.account_detect.detect_account_usernames_for_packages",
                 side_effect=AssertionError("legacy bulk scan"),
             ):
            self.assertEqual(
                package_username.safe_detect_username_for_package("com.x.y"),
                "Unknown",
            )


class CollectSafeUsernamesTests(unittest.TestCase):
    def test_collect_respects_total_deadline(self) -> None:
        slow_packages = [f"com.moons.lites{c}" for c in "abcdef"]

        def _slow_read(*_a, **_k):
            time.sleep(0.4)
            return None

        with mock.patch("agent.root_access.read_root_file", side_effect=_slow_read):
            started = time.monotonic()
            result = package_username.collect_safe_usernames_for_packages(
                slow_packages,
                per_package_timeout_seconds=1.0,
                total_deadline_seconds=1.0,
            )
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 2.0)
        for pkg in slow_packages:
            self.assertEqual(result[pkg], "Unknown")


class PackageMenuRenderingFreezeTests(unittest.TestCase):
    def test_config_menu_package_render_does_not_call_root_or_detector(self) -> None:
        cfg = _cfg([
            package_entry(f"com.moons.lites{c}", "", True, "not_set")
            for c in "abcdefgh"
        ])
        with mock.patch("agent.commands._is_interactive", return_value=True), \
             mock.patch("agent.commands.safe_io.safe_prompt", return_value="0"), \
             mock.patch(
                 "agent.package_username.detect_package_username_quick",
                 side_effect=AssertionError(
                     "menu render must not run detector"
                 ),
             ), \
             mock.patch(
                 "agent.root_access.read_root_file",
                 side_effect=AssertionError("menu render must not read root"),
             ), \
             mock.patch(
                 "agent.commands._safe_refresh_account_mapping_entries",
                 side_effect=AssertionError("legacy refresh mapping"),
             ), \
             mock.patch(
                 "agent.commands._package_menu_refresh_mapping",
                 side_effect=AssertionError("legacy refresh mapping menu"),
             ), \
             redirect_stdout(io.StringIO()) as out:
            started = time.monotonic()
            result = _config_menu_package(cfg)
            elapsed = time.monotonic() - started

        self.assertIs(result, cfg)
        self.assertLess(elapsed, 1.0, "menu render must be near-instant")
        text = out.getvalue()
        self.assertIn("Auto Detect Package", text)
        self.assertIn("Add Package", text)
        self.assertIn("Remove Package", text)
        self.assertNotIn("Edit Username Label", text)
        plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
        self.assertIsNone(re.search(r"(?m)^\s*4\.\s+Edit Username", plain))
        self.assertIn("Unknown", text)
        self.assertNotIn("Refresh Mapping", text)
        self.assertNotIn("Account Mapping", text)
        self.assertNotIn("Detect / Refresh", text)


class AutoDetectFreezeRegressionTests(unittest.TestCase):
    def test_auto_detect_does_not_call_refresh_mapping_or_legacy_username_scan(
        self,
    ) -> None:
        cfg = _cfg([package_entry("com.roblox.client", "", True, "not_set")])
        candidates = [
            mock.Mock(
                package=f"com.moons.lites{c}", app_name=f"Lite {c}", launchable=True
            )
            for c in "abcd"
        ]
        with mock.patch(
            "agent.commands._gather_roblox_candidates_for_ui",
            return_value=candidates,
        ), \
             mock.patch(
                 "agent.commands.safe_io.safe_prompt", return_value="a"
             ), \
             mock.patch(
                 "agent.commands.save_config",
                 side_effect=lambda data: data,
             ), \
             mock.patch(
                 "agent.commands._safe_refresh_account_mapping_entries",
                 side_effect=AssertionError("legacy refresh mapping"),
             ), \
             mock.patch(
                 "agent.commands._package_menu_refresh_mapping",
                 side_effect=AssertionError("legacy refresh mapping menu"),
             ), \
             mock.patch(
                 "agent.commands._package_menu_detect_refresh",
                 side_effect=AssertionError("legacy detect/refresh"),
             ), \
             mock.patch(
                 "agent.commands._auto_detect_cookies_for_entries",
                 side_effect=AssertionError("cookie scan"),
             ), \
             mock.patch(
                 "agent.commands.account_detect.detect_account_username",
                 side_effect=AssertionError("legacy username scan"),
             ), \
             mock.patch(
                 "agent.commands.account_detect.detect_account_usernames_for_packages",
                 side_effect=AssertionError("legacy username bulk scan"),
             ), \
             mock.patch(
                 "agent.package_username.safe_detect_username_for_package",
                 return_value="Unknown",
             ) as safe_detector:
            result = _package_menu_auto_detect(cfg)

        added = {e["package"] for e in result["roblox_packages"]}
        for c in candidates:
            self.assertIn(c.package, added)
        for c in candidates:
            safe_detector.assert_any_call(
                c.package, timeout_seconds=mock.ANY
            )

    def test_auto_detect_caches_safe_username_for_new_packages(self) -> None:
        cfg = _cfg([package_entry("com.roblox.client", "", True, "not_set")])
        candidate = mock.Mock(
            package="com.moons.litesc", app_name="Lite C", launchable=True
        )
        with mock.patch(
            "agent.commands._gather_roblox_candidates_for_ui",
            return_value=[candidate],
        ), \
             mock.patch(
                 "agent.commands.safe_io.safe_prompt", return_value="a"
             ), \
             mock.patch(
                 "agent.commands.save_config", side_effect=lambda data: data
             ), \
             mock.patch(
                 "agent.package_username.safe_detect_username_for_package",
                 return_value="deng1629",
             ):
            result = _package_menu_auto_detect(cfg)

        cache = result.get("package_username_cache") or {}
        self.assertEqual(cache.get("com.moons.litesc"), "deng1629")
        entry = next(
            e
            for e in result["roblox_packages"]
            if e["package"] == "com.moons.litesc"
        )
        self.assertEqual(entry["account_username"], "deng1629")
        self.assertEqual(entry["username_source"], "detected_safe_pref")

    def test_auto_detect_post_add_pass_respects_global_budget(self) -> None:
        cfg = _cfg([package_entry("com.roblox.client", "", True, "not_set")])
        candidates = [
            mock.Mock(
                package=f"com.moons.lites{c}",
                app_name=f"Lite {c}",
                launchable=True,
            )
            for c in "abcdefgh"
        ]

        def _slow_safe(*_a, **_k):
            time.sleep(0.6)
            return "Unknown"

        with mock.patch(
            "agent.commands._gather_roblox_candidates_for_ui",
            return_value=candidates,
        ), \
             mock.patch(
                 "agent.commands.safe_io.safe_prompt", return_value="a"
             ), \
             mock.patch(
                 "agent.commands.save_config", side_effect=lambda data: data
             ), \
             mock.patch(
                 "agent.package_username.safe_detect_username_for_package",
                 side_effect=_slow_safe,
             ):
            started = time.monotonic()
            result = _package_menu_auto_detect(cfg)
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 6.5)
        added = {e["package"] for e in result["roblox_packages"]}
        for c in candidates:
            self.assertIn(c.package, added)


class ManualAddFreezeRegressionTests(unittest.TestCase):
    def test_manual_add_does_not_call_refresh_mapping(self) -> None:
        cfg = _cfg([package_entry("com.roblox.client", "", True, "not_set")])
        prompts = iter(["m", "y"])
        with mock.patch(
            "agent.commands._gather_roblox_candidates_for_ui", return_value=[]
        ), \
             mock.patch(
                 "agent.commands._prompt_manual_package",
                 return_value="com.moons.litesc",
             ), \
             mock.patch(
                 "agent.commands.android.package_installed", return_value=True
             ), \
             mock.patch(
                 "agent.commands.safe_io.safe_prompt",
                 side_effect=lambda *_a, **_k: next(prompts),
             ), \
             mock.patch(
                 "agent.commands.save_config", side_effect=lambda data: data
             ), \
             mock.patch(
                 "agent.commands._safe_refresh_account_mapping_entries",
                 side_effect=AssertionError("refresh mapping"),
             ), \
             mock.patch(
                 "agent.commands._package_menu_refresh_mapping",
                 side_effect=AssertionError("refresh mapping menu"),
             ), \
             mock.patch(
                 "agent.commands._package_menu_detect_refresh",
                 side_effect=AssertionError("legacy detect refresh"),
             ), \
             mock.patch(
                 "agent.commands._detect_or_prompt_account_username",
                 side_effect=AssertionError("legacy detect or prompt"),
             ), \
             mock.patch(
                 "agent.commands._auto_detect_cookies_for_entries",
                 side_effect=AssertionError("cookie scan"),
             ), \
             mock.patch(
                 "agent.package_username.safe_detect_username_for_package",
                 return_value="Unknown",
             ):
            result = _package_menu_add(cfg)
        added = result["roblox_packages"][-1]
        self.assertEqual(added["package"], "com.moons.litesc")

    def test_manual_add_saves_detected_username_automatically(self) -> None:
        cfg = _cfg([package_entry("com.roblox.client", "", True, "not_set")])
        prompts = iter(["m", "y"])
        with mock.patch(
            "agent.commands._gather_roblox_candidates_for_ui", return_value=[]
        ), \
             mock.patch(
                 "agent.commands._prompt_manual_package",
                 return_value="com.moons.litesc",
             ), \
             mock.patch(
                 "agent.commands.android.package_installed", return_value=True
             ), \
             mock.patch(
                 "agent.commands.safe_io.safe_prompt",
                 side_effect=lambda *_a, **_k: next(prompts),
             ), \
             mock.patch(
                 "agent.commands.save_config", side_effect=lambda data: data
             ), \
             mock.patch(
                 "agent.package_username.safe_detect_username_for_package",
                 return_value="dengauto",
             ):
            result = _package_menu_add(cfg)
        added = result["roblox_packages"][-1]
        self.assertEqual(added["package"], "com.moons.litesc")
        self.assertEqual(added["account_username"], "dengauto")
        self.assertEqual(added["username_source"], "detected_safe_pref")


class BoundedPostAddHelperTests(unittest.TestCase):
    def test_bounded_helper_skips_when_no_packages(self) -> None:
        cfg = _cfg()
        with mock.patch(
            "agent.package_username.collect_safe_usernames_for_packages",
            side_effect=AssertionError("must not run"),
        ):
            result = _bounded_post_add_username_detection(cfg, [])
        self.assertIs(result, cfg)

    def test_bounded_helper_writes_cache_and_account_username(self) -> None:
        cfg = _cfg([
            package_entry("com.moons.litesc", "", True, "not_set"),
        ])
        with mock.patch(
            "agent.package_username.collect_safe_usernames_for_packages",
            return_value={"com.moons.litesc": "deng1629"},
        ), \
             mock.patch(
                 "agent.commands.save_config", side_effect=lambda data: data
             ):
            result = _bounded_post_add_username_detection(
                cfg, ["com.moons.litesc"]
            )
        cache = result.get("package_username_cache") or {}
        self.assertEqual(cache.get("com.moons.litesc"), "deng1629")
        entry = next(
            e
            for e in result["roblox_packages"]
            if e["package"] == "com.moons.litesc"
        )
        self.assertEqual(entry["account_username"], "deng1629")
        self.assertEqual(entry["username_source"], "detected_safe_pref")

    def test_bounded_helper_replaces_legacy_manual_label_when_detected(self) -> None:
        cfg = _cfg([
            package_entry("com.moons.litesc", "DENGLABEL", True, "manual"),
        ])
        with mock.patch(
            "agent.package_username.collect_safe_usernames_for_packages",
            return_value={"com.moons.litesc": "detected"},
        ), \
             mock.patch(
                 "agent.commands.save_config", side_effect=lambda data: data
             ):
            result = _bounded_post_add_username_detection(
                cfg, ["com.moons.litesc"]
            )
        entry = next(
            e
            for e in result["roblox_packages"]
            if e["package"] == "com.moons.litesc"
        )
        self.assertEqual(entry["account_username"], "detected")
        self.assertEqual(entry["username_source"], "detected_safe_pref")


if __name__ == "__main__":
    unittest.main()
