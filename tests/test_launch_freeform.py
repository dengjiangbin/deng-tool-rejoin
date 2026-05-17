"""Launch-with-freeform-mode behaviour.

Probe p-70ba19b440 (Samsung SM-N9810, Android 13) confirmed:

* ``cmd activity start-activity --windowingMode <WINDOWING_MODE>`` is
  available in this build's ``am`` / ``cmd activity`` verbs.
* ``settings global enable_freeform_support`` / ``force_resizable_activities``
  / ``freeform_window_management`` are all ``1`` on the host.
* App Cloner ``app_cloner_current_window_*`` keys exist in the
  ``<package>_preferences.xml`` file.

For the OS to honor App Cloner's saved window bounds, the activity must
be launched in ``WINDOWING_MODE_FREEFORM`` (5).  Plain ``am start`` puts
the activity into fullscreen mode and the bounds are ignored.

These tests pin the new launch contract:

1. ``launch_url`` and ``launch_app`` first try ``--windowingMode 5``.
2. If the framework rejects that flag, both transparently fall back to
   the un-flagged form so callers never see a launch regression.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import android  # noqa: E402


def _ok(args: tuple[str, ...]) -> android.CommandResult:
    return android.CommandResult(args, 0, "Starting", "")


def _fail(args: tuple[str, ...], stderr: str = "Error type 8") -> android.CommandResult:
    return android.CommandResult(args, 1, "", stderr)


class LaunchUrlPassesWindowingModeTests(unittest.TestCase):
    def test_first_attempt_includes_windowingMode_5(self) -> None:
        seen: list[list[str]] = []

        def fake(args, *, timeout):  # noqa: ARG001
            seen.append(list(args))
            return _ok(tuple(args))

        with patch.object(android, "run_command", side_effect=fake):
            res = android.launch_url(
                "com.moons.litesc",
                "https://www.roblox.com/share?code=abc&type=Server",
                "web_url",
            )
        self.assertTrue(res.ok)
        self.assertEqual(len(seen), 1, msg=f"expected single call; got {seen}")
        # The flag must come BEFORE the action so am parses it as part
        # of start-activity options.
        cmd = seen[0]
        self.assertIn("--windowingMode", cmd)
        self.assertEqual(cmd[cmd.index("--windowingMode") + 1], "5")
        # Package is still the last positional.
        self.assertEqual(cmd[-1], "com.moons.litesc")

    def test_falls_back_without_flag_when_rejected(self) -> None:
        seen: list[list[str]] = []

        def fake(args, *, timeout):  # noqa: ARG001
            seen.append(list(args))
            if "--windowingMode" in args:
                return _fail(tuple(args), "Error type 8 / Bad component name")
            return _ok(tuple(args))

        with patch.object(android, "run_command", side_effect=fake):
            res = android.launch_url(
                "com.moons.litesc",
                "https://www.roblox.com/share?code=abc&type=Server",
                "web_url",
            )
        self.assertTrue(res.ok)
        # Two calls: the freeform attempt, then the unflagged retry.
        self.assertEqual(len(seen), 2)
        self.assertIn("--windowingMode", seen[0])
        self.assertNotIn("--windowingMode", seen[1])


class LaunchUrlGenericTests(unittest.TestCase):
    def test_passes_flag_then_retries(self) -> None:
        seen: list[list[str]] = []

        def fake(args, *, timeout):  # noqa: ARG001
            seen.append(list(args))
            if "--windowingMode" in args:
                return _fail(tuple(args))
            return _ok(tuple(args))

        with patch.object(android, "run_command", side_effect=fake):
            res = android.launch_url_generic(
                "https://www.roblox.com/share?code=abc&type=Server",
                "web_url",
            )
        self.assertTrue(res.ok)
        self.assertEqual(len(seen), 2)


class LaunchAppMethod1PassesFlagTests(unittest.TestCase):
    def test_main_intent_first_tries_freeform(self) -> None:
        seen: list[list[str]] = []

        def fake(args, *, timeout):  # noqa: ARG001
            seen.append(list(args))
            return _ok(tuple(args))

        with patch("agent.android._find_command", return_value="/system/bin/am"), \
             patch.object(android, "run_command", side_effect=fake):
            res = android.launch_app("com.moons.litesc")
        self.assertTrue(res.ok)
        self.assertEqual(len(seen), 1)
        self.assertIn("--windowingMode", seen[0])
        self.assertEqual(seen[0][seen[0].index("--windowingMode") + 1], "5")


if __name__ == "__main__":
    unittest.main()
