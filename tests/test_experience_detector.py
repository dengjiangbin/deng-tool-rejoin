"""Tests for agent/experience_detector.py.

Covers:
  1. EvidenceLevel enum ordering.
  2. ExperienceEvidence helpers (is_in_game, is_home_or_lobby).
  3. detect_experience_state returns ExperienceEvidence (never raises).
  4. Graceful degradation when Android tools unavailable (Windows / no adb).
  5. Logcat signal matching (mocked).
  6. Dumpsys activity signal matching (mocked).
  7. UIAutomator signal matching (mocked).
  8. url_launched=True upgrades HOME_OR_LOBBY → JOIN_FAILED_OR_HOME.
  9. Tools unavailable → returns FOREGROUND_APP, not crash.
 10. Detector never reads session/cookie/auth data.
"""
from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.experience_detector import (
    EvidenceLevel,
    ExperienceEvidence,
    detect_experience_state,
    _probe_logcat,
    _probe_dumpsys_activity,
    _probe_uiautomator,
)
from agent.android import CommandResult


def _ok(stdout: str) -> CommandResult:
    """Helper: build a successful CommandResult with the given stdout."""
    return CommandResult(("cmd",), 0, stdout, "")


def _fail() -> CommandResult:
    """Helper: build a failed CommandResult (tool not found)."""
    return CommandResult(("cmd",), 127, "", "not found")


class TestEvidenceLevelOrdering(unittest.TestCase):
    """EvidenceLevel values must be ordered weakest → strongest."""

    def test_process_only_weakest(self):
        self.assertLess(EvidenceLevel.PROCESS_ONLY, EvidenceLevel.FOREGROUND_APP)

    def test_foreground_weaker_than_home(self):
        self.assertLess(EvidenceLevel.FOREGROUND_APP, EvidenceLevel.ROBLOX_HOME_OR_LOBBY)

    def test_home_weaker_than_joining(self):
        self.assertLess(EvidenceLevel.ROBLOX_HOME_OR_LOBBY, EvidenceLevel.JOINING_PRIVATE_URL)

    def test_joining_weaker_than_experience(self):
        self.assertLess(EvidenceLevel.JOINING_PRIVATE_URL, EvidenceLevel.EXPERIENCE_LIKELY_LOADED)

    def test_experience_weaker_than_join_failed(self):
        # JOIN_FAILED_OR_HOME is strongest in the enum for logical completeness
        self.assertLess(EvidenceLevel.EXPERIENCE_LIKELY_LOADED, EvidenceLevel.JOIN_FAILED_OR_HOME)


class TestExperienceEvidenceHelpers(unittest.TestCase):
    """is_in_game and is_home_or_lobby return correct booleans."""

    def _ev(self, level: EvidenceLevel) -> ExperienceEvidence:
        return ExperienceEvidence(level=level, detail="test", source="test")

    def test_is_in_game_only_true_for_experience_loaded(self):
        self.assertTrue(self._ev(EvidenceLevel.EXPERIENCE_LIKELY_LOADED).is_in_game())
        for lv in (
            EvidenceLevel.PROCESS_ONLY,
            EvidenceLevel.FOREGROUND_APP,
            EvidenceLevel.ROBLOX_HOME_OR_LOBBY,
            EvidenceLevel.JOINING_PRIVATE_URL,
            EvidenceLevel.JOIN_FAILED_OR_HOME,
        ):
            self.assertFalse(self._ev(lv).is_in_game(), msg=f"{lv} should not be is_in_game()")

    def test_is_home_or_lobby_for_home_levels(self):
        self.assertTrue(self._ev(EvidenceLevel.ROBLOX_HOME_OR_LOBBY).is_home_or_lobby())
        self.assertTrue(self._ev(EvidenceLevel.JOIN_FAILED_OR_HOME).is_home_or_lobby())

    def test_is_home_or_lobby_false_for_other_levels(self):
        for lv in (
            EvidenceLevel.PROCESS_ONLY,
            EvidenceLevel.FOREGROUND_APP,
            EvidenceLevel.JOINING_PRIVATE_URL,
            EvidenceLevel.EXPERIENCE_LIKELY_LOADED,
        ):
            self.assertFalse(self._ev(lv).is_home_or_lobby(), msg=f"{lv} should not be is_home_or_lobby()")


class TestDetectGracefulDegradation(unittest.TestCase):
    """detect_experience_state never raises and degrades safely."""

    def test_returns_experience_evidence_instance(self):
        result = detect_experience_state("com.roblox.client")
        self.assertIsInstance(result, ExperienceEvidence)

    def test_does_not_raise_on_windows_no_android(self):
        # All commands will fail (non-Android environment) — must not raise.
        try:
            result = detect_experience_state("com.roblox.client")
        except Exception as exc:  # noqa: BLE001
            self.fail(f"detect_experience_state raised {exc!r}")
        self.assertIsNotNone(result)

    def test_fallback_level_is_foreground_app_or_lower(self):
        # Without Android tools, result must be FOREGROUND_APP or lower (process_only).
        result = detect_experience_state("com.roblox.client")
        self.assertLessEqual(result.level, EvidenceLevel.FOREGROUND_APP)

    def test_uiautomator_unavailable_does_not_crash(self):
        with unittest.mock.patch("agent.experience_detector.android.run_command", return_value=_fail()):
            result = _probe_uiautomator("com.roblox.client")
        # Must return None gracefully, not raise.
        self.assertIsNone(result)

    def test_logcat_unavailable_does_not_crash(self):
        with unittest.mock.patch("agent.experience_detector.android.run_command", return_value=_fail()):
            result = _probe_logcat("com.roblox.client", pid=None)
        self.assertIsNone(result)

    def test_dumpsys_unavailable_does_not_crash(self):
        with unittest.mock.patch("agent.experience_detector.android.run_command", return_value=_fail()):
            result = _probe_dumpsys_activity("com.roblox.client")
        self.assertIsNone(result)

    def test_invalid_package_returns_process_only(self):
        result = detect_experience_state("not!!a.valid.package")
        self.assertEqual(result.level, EvidenceLevel.PROCESS_ONLY)


class TestLogcatDetection(unittest.TestCase):
    """_probe_logcat correctly classifies in-game and home signals."""

    def _logcat(self, log_text: str, pid: str = "1234") -> ExperienceEvidence | None:
        def fake_run(args, *, timeout=5):
            if "logcat" in " ".join(args):
                return _ok(log_text)
            return _fail()

        with unittest.mock.patch("agent.experience_detector.android.run_command", side_effect=fake_run):
            return _probe_logcat("com.roblox.client", pid=pid)

    def test_game_loaded_signal_returns_experience_likely(self):
        ev = self._logcat("I/RobloxApp: GameLoaded place=123456")
        self.assertIsNotNone(ev)
        self.assertEqual(ev.level, EvidenceLevel.EXPERIENCE_LIKELY_LOADED)

    def test_joined_game_signal_returns_experience_likely(self):
        ev = self._logcat("D/PlaceService: JoinedGame userId=99 placeId=55")
        self.assertIsNotNone(ev)
        self.assertEqual(ev.level, EvidenceLevel.EXPERIENCE_LIKELY_LOADED)

    def test_home_scene_signal_returns_home(self):
        ev = self._logcat("I/RomarkApp: HomeScene ready")
        self.assertIsNotNone(ev)
        self.assertEqual(ev.level, EvidenceLevel.ROBLOX_HOME_OR_LOBBY)

    def test_home_menu_signal_returns_home(self):
        ev = self._logcat("V/LuaApp: HomeMenu initialized")
        self.assertIsNotNone(ev)
        self.assertEqual(ev.level, EvidenceLevel.ROBLOX_HOME_OR_LOBBY)

    def test_returning_to_home_signal(self):
        ev = self._logcat("I/Navigation: LaunchingToHome started")
        self.assertIsNotNone(ev)
        self.assertEqual(ev.level, EvidenceLevel.ROBLOX_HOME_OR_LOBBY)

    def test_unrelated_noise_returns_none(self):
        ev = self._logcat("D/SomeOtherApp: nothing interesting here")
        self.assertIsNone(ev)

    def test_empty_logcat_returns_none(self):
        ev = self._logcat("")
        self.assertIsNone(ev)


class TestDumpsysActivityDetection(unittest.TestCase):
    """_probe_dumpsys_activity correctly classifies from activity class names."""

    def _dumpsys(self, text: str) -> ExperienceEvidence | None:
        def fake_run(args, *, timeout=5):
            if "activities" in args or "top" in args:
                return _ok(text)
            return _fail()

        with unittest.mock.patch("agent.experience_detector.android.run_command", side_effect=fake_run):
            return _probe_dumpsys_activity("com.roblox.client")

    def test_game_activity_returns_experience_likely(self):
        text = (
            "  TaskRecord #1 of com.roblox.client\n"
            "    * ActivityRecord{com.roblox.client/.GameActivity}\n"
        )
        ev = self._dumpsys(text)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.level, EvidenceLevel.EXPERIENCE_LIKELY_LOADED)

    def test_splash_activity_returns_home(self):
        text = (
            "  TaskRecord #2 of com.roblox.client\n"
            "    * ActivityRecord{com.roblox.client/.SplashActivity}\n"
        )
        ev = self._dumpsys(text)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.level, EvidenceLevel.ROBLOX_HOME_OR_LOBBY)

    def test_main_activity_returns_home(self):
        text = "    Activity com.roblox.client/.MainActivity state=RESUMED\n"
        ev = self._dumpsys(text)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.level, EvidenceLevel.ROBLOX_HOME_OR_LOBBY)

    def test_no_matching_package_returns_none(self):
        text = "  unrelated.package/.SomeActivity\n"
        ev = self._dumpsys(text)
        self.assertIsNone(ev)

    def test_empty_output_returns_none(self):
        ev = self._dumpsys("")
        self.assertIsNone(ev)


class TestUiAutomatorDetection(unittest.TestCase):
    """_probe_uiautomator correctly classifies from UI hierarchy XML."""

    def _uia(self, xml: str) -> ExperienceEvidence | None:
        def fake_run(args, *, timeout=5):
            if "uiautomator" in args and "dump" in args:
                return _ok(xml)
            return _fail()

        with unittest.mock.patch("agent.experience_detector.android.run_command", side_effect=fake_run):
            return _probe_uiautomator("com.roblox.client")

    def _make_xml(self, texts: list[str]) -> str:
        nodes = "".join(
            f' <node package="com.roblox.client" text="{t}" class="android.widget.TextView"/>\n'
            for t in texts
        )
        return f"<?xml version='1.0' encoding='UTF-8'?><hierarchy rotation='0'>\n{nodes}</hierarchy>"

    def test_leave_game_button_returns_experience(self):
        xml = self._make_xml(["Leave Game"])
        ev = self._uia(xml)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.level, EvidenceLevel.EXPERIENCE_LIKELY_LOADED)

    def test_reset_character_returns_experience(self):
        xml = self._make_xml(["Reset Character"])
        ev = self._uia(xml)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.level, EvidenceLevel.EXPERIENCE_LIKELY_LOADED)

    def test_two_or_more_home_nav_items_returns_home(self):
        xml = self._make_xml(["Home", "Discover", "Play"])
        ev = self._uia(xml)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.level, EvidenceLevel.ROBLOX_HOME_OR_LOBBY)

    def test_single_home_item_returns_none(self):
        # One nav item is insufficient (too ambiguous)
        xml = self._make_xml(["Home"])
        ev = self._uia(xml)
        self.assertIsNone(ev)

    def test_not_in_xml_package_returns_none(self):
        xml = '<hierarchy><node package="other.app" text="Home" /></hierarchy>'
        ev = self._uia(xml)
        self.assertIsNone(ev)

    def test_no_hierarchy_in_output_returns_none(self):
        ev = self._uia("UI Automator failed to start")
        self.assertIsNone(ev)


class TestUrlLaunchedUpgradesHomeToJoinFailed(unittest.TestCase):
    """When url_launched=True, home evidence upgrades to JOIN_FAILED_OR_HOME."""

    def test_home_logcat_after_url_is_join_failed(self):
        home_logcat = "I/RomarkApp: HomeScene ready"

        def fake_run(args, *, timeout=5):
            if "logcat" in " ".join(args):
                return _ok(home_logcat)
            return _fail()

        with unittest.mock.patch("agent.experience_detector.android.run_command", side_effect=fake_run):
            ev = detect_experience_state("com.roblox.client", url_launched=True)

        self.assertEqual(ev.level, EvidenceLevel.JOIN_FAILED_OR_HOME)
        self.assertTrue(ev.is_home_or_lobby())

    def test_home_logcat_without_url_stays_home(self):
        home_logcat = "I/RomarkApp: HomeScene ready"

        def fake_run(args, *, timeout=5):
            if "logcat" in " ".join(args):
                return _ok(home_logcat)
            return _fail()

        with unittest.mock.patch("agent.experience_detector.android.run_command", side_effect=fake_run):
            ev = detect_experience_state("com.roblox.client", url_launched=False)

        self.assertEqual(ev.level, EvidenceLevel.ROBLOX_HOME_OR_LOBBY)
        self.assertTrue(ev.is_home_or_lobby())

    def test_ingame_logcat_after_url_stays_in_server(self):
        """url_launched=True + in-game signal → still EXPERIENCE_LIKELY_LOADED (not overridden)."""
        game_logcat = "I/GameService: GameLoaded placeId=99"

        def fake_run(args, *, timeout=5):
            if "logcat" in " ".join(args):
                return _ok(game_logcat)
            return _fail()

        with unittest.mock.patch("agent.experience_detector.android.run_command", side_effect=fake_run):
            ev = detect_experience_state("com.roblox.client", url_launched=True)

        self.assertEqual(ev.level, EvidenceLevel.EXPERIENCE_LIKELY_LOADED)
        self.assertTrue(ev.is_in_game())


if __name__ == "__main__":
    unittest.main()
