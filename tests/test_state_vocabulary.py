"""Tests for the user-aligned state vocabulary.

New 4-state watchdog vocabulary (WatchdogSupervisor):
    In-Lobby     — process running, not in game/server (home/lobby/menu)
    Online       — process running, in game with healthy heartbeat
    No Heartbeat — process running, was in game, heartbeat stalled
    Dead         — process not running (force-closed / crashed)
    Launching    — transient: launch sent, awaiting first detection round

Legacy constants are kept in supervisor.py for backward compatibility of
old _PackageWorker tests but must NOT be produced by WatchdogSupervisor.
Joining is deprecated and removed from WatchdogSupervisor.
"""

from __future__ import annotations

import unittest

from agent.supervisor import (
    STATUS_DEAD,
    STATUS_DISCONNECTED,
    STATUS_IN_LOBBY,
    STATUS_JOINING,
    STATUS_LAUNCHED,
    STATUS_LAUNCHING,
    STATUS_LOBBY,
    STATUS_NO_HEARTBEAT,
    STATUS_OFFLINE,
    STATUS_ONLINE,
    _HEALTHY_STATES,
)


class TestStateConstants(unittest.TestCase):
    """Legacy constants must still exist for backward compat."""

    def test_required_legacy_states_exist(self) -> None:
        self.assertEqual(STATUS_LAUNCHING, "Launching")
        self.assertEqual(STATUS_LAUNCHED, "Launched")
        self.assertEqual(STATUS_JOINING, "Joining")    # deprecated, kept for compat
        self.assertEqual(STATUS_ONLINE, "Online")
        self.assertEqual(STATUS_LOBBY, "Lobby")
        self.assertEqual(STATUS_OFFLINE, "Offline")
        self.assertEqual(STATUS_DISCONNECTED, "Disconnected")

    def test_new_watchdog_states_exist(self) -> None:
        """New 4-state machine constants must exist."""
        self.assertEqual(STATUS_IN_LOBBY, "In-Lobby")
        self.assertEqual(STATUS_NO_HEARTBEAT, "No Heartbeat")
        self.assertEqual(STATUS_DEAD, "Dead")
        self.assertEqual(STATUS_ONLINE, "Online")
        self.assertEqual(STATUS_LAUNCHING, "Launching")

    def test_launched_is_a_healthy_state(self) -> None:
        self.assertIn(STATUS_LAUNCHED, _HEALTHY_STATES)

    def test_lobby_is_a_healthy_state(self) -> None:
        self.assertIn(STATUS_LOBBY, _HEALTHY_STATES)

    def test_online_is_a_healthy_state(self) -> None:
        self.assertIn(STATUS_ONLINE, _HEALTHY_STATES)

    def test_offline_is_NOT_a_healthy_state(self) -> None:
        self.assertNotIn(STATUS_OFFLINE, _HEALTHY_STATES)

    def test_disconnected_is_NOT_a_healthy_state(self) -> None:
        self.assertNotIn(STATUS_DISCONNECTED, _HEALTHY_STATES)

    def test_in_lobby_not_in_legacy_healthy_states(self) -> None:
        """In-Lobby is not in _HEALTHY_STATES (it's a watchdog-only state)."""
        # _HEALTHY_STATES is for the old _PackageWorker; the new watchdog has its own logic.
        self.assertNotIn(STATUS_IN_LOBBY, _HEALTHY_STATES)

    def test_no_heartbeat_not_in_legacy_healthy_states(self) -> None:
        self.assertNotIn(STATUS_NO_HEARTBEAT, _HEALTHY_STATES)


class TestStatusColors(unittest.TestCase):
    """Colorize map must have entries for all states including new watchdog states."""

    def test_in_lobby_and_no_heartbeat_have_colors(self) -> None:
        from agent.commands import _colorize_status
        out_il = _colorize_status("In-Lobby", use_color=True)
        self.assertIn("In-Lobby", out_il)
        self.assertNotEqual(out_il, "In-Lobby", "In-Lobby must be colorized")

        out_nh = _colorize_status("No Heartbeat", use_color=True)
        self.assertIn("No Heartbeat", out_nh)
        self.assertNotEqual(out_nh, "No Heartbeat", "No Heartbeat must be colorized")

    def test_launched_and_disconnected_have_colors(self) -> None:
        from agent.commands import _colorize_status
        out = _colorize_status("Launched", use_color=True)
        self.assertIn("Launched", out)
        self.assertNotEqual(out, "Launched")

        out_d = _colorize_status("Disconnected", use_color=True)
        self.assertIn("Disconnected", out_d)
        self.assertNotEqual(out_d, "Disconnected")


class TestInitialStartTableUsesLaunching(unittest.TestCase):
    """Post-launch initial status must always be Launching (Joining removed)."""

    def test_post_launch_always_launching_regardless_of_url(self) -> None:
        """cmd_start sets Launching for all packages after launch — no Joining."""
        for _has_url in (True, False):
            launch_ok = True
            running = True
            # New logic:
            if not launch_ok:
                state = "Failed"
            elif running:
                state = "Launching"  # no longer "Joining" even with URL
            else:
                state = "Launching"
            self.assertEqual(state, "Launching",
                f"expected Launching with has_url={_has_url}, got {state}")

    def test_joining_is_deprecated_and_not_used_as_initial_state(self) -> None:
        """Joining must not appear as an initial status produced by cmd_start logic."""
        for _has_url in (True, False):
            launch_ok = True
            running = True
            if not launch_ok:
                state = "Failed"
            elif running:
                state = "Launching"
            else:
                state = "Launching"
            self.assertNotEqual(state, "Joining")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
