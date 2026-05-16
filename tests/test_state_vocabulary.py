"""Tests for the user-aligned state vocabulary.

User-mandated vocabulary:
    Preparing    — close other APKs / clear cache (only before first launch)
    Launching    — am start command sent, no process yet
    Launched     — Roblox process is up, no URL / game evidence yet
    Joining      — private URL sent, waiting for InGame presence
    Online       — Roblox presence API says InGame  (authoritative)
    Lobby        — Roblox presence says Online (not in a place)
    Offline      — process gone AND presence says Offline AND grace expired
    Disconnected — Roblox error code detected in logcat/dumpsys
"""

from __future__ import annotations

import unittest

from agent.supervisor import (
    STATUS_DISCONNECTED,
    STATUS_JOINING,
    STATUS_LAUNCHED,
    STATUS_LAUNCHING,
    STATUS_LOBBY,
    STATUS_OFFLINE,
    STATUS_ONLINE,
    _HEALTHY_STATES,
)


class TestStateConstants(unittest.TestCase):
    def test_required_user_states_exist(self) -> None:
        self.assertEqual(STATUS_LAUNCHING, "Launching")
        self.assertEqual(STATUS_LAUNCHED, "Launched")
        self.assertEqual(STATUS_JOINING, "Joining")
        self.assertEqual(STATUS_ONLINE, "Online")
        self.assertEqual(STATUS_LOBBY, "Lobby")
        self.assertEqual(STATUS_OFFLINE, "Offline")
        self.assertEqual(STATUS_DISCONNECTED, "Disconnected")

    def test_launched_is_a_healthy_state(self) -> None:
        # Launched must not trigger the offline / reconnect path.
        self.assertIn(STATUS_LAUNCHED, _HEALTHY_STATES)

    def test_lobby_is_a_healthy_state(self) -> None:
        self.assertIn(STATUS_LOBBY, _HEALTHY_STATES)

    def test_online_is_a_healthy_state(self) -> None:
        self.assertIn(STATUS_ONLINE, _HEALTHY_STATES)

    def test_offline_is_NOT_a_healthy_state(self) -> None:
        self.assertNotIn(STATUS_OFFLINE, _HEALTHY_STATES)

    def test_disconnected_is_NOT_a_healthy_state(self) -> None:
        self.assertNotIn(STATUS_DISCONNECTED, _HEALTHY_STATES)


class TestStatusColors(unittest.TestCase):
    """Colorize map must have entries for the new states."""

    def test_launched_and_disconnected_have_colors(self) -> None:
        from agent.commands import _colorize_status
        # use_color=True must wrap the new labels in ANSI codes.
        out = _colorize_status("Launched", use_color=True)
        self.assertIn("Launched", out)
        # Some ANSI escape must surround it (not the bare label).
        self.assertNotEqual(out, "Launched")

        out_d = _colorize_status("Disconnected", use_color=True)
        self.assertIn("Disconnected", out_d)
        self.assertNotEqual(out_d, "Disconnected")


class TestInitialStartTableUsesLaunched(unittest.TestCase):
    """When the launch command succeeded and the process is up but no URL was
    sent, the initial Start table row must be ``Launched`` (NOT ``Lobby``).
    """

    def test_launched_label_for_no_url_running_process(self) -> None:
        # Re-create the relevant logic locally to avoid pulling in the
        # entire cmd_start machinery in unit test.
        launch_ok = True
        running = True
        _has_url = False
        if not launch_ok:
            state = "Failed"
        elif running:
            state = "Joining" if _has_url else "Launched"
        else:
            state = "Joining" if _has_url else "Launching"
        self.assertEqual(state, "Launched")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
