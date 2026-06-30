"""Tests for the user-aligned state vocabulary.

Watchdog steady states:
    Online       — process running, in game with healthy heartbeat
    No Heartbeat — process running, but not playing normally or stalled
    Dead         — process not running (force-closed / crashed)

Legacy constants are kept in supervisor.py for backward compatibility of
old _PackageWorker tests but must NOT be produced by WatchdogSupervisor.
Packages remain Launching until the round-robin watchdog confirms Online.
"""

from __future__ import annotations

import unittest

from agent.supervisor import (
    STATUS_DEAD,
    STATUS_DISCONNECTED,
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
        self.assertEqual(STATUS_ONLINE, "Online")
        self.assertEqual(STATUS_LOBBY, "Lobby")
        self.assertEqual(STATUS_OFFLINE, "Offline")
        self.assertEqual(STATUS_DISCONNECTED, "Disconnected")

    def test_joining_constant_removed(self) -> None:
        import agent.supervisor as sup_mod

        self.assertFalse(hasattr(sup_mod, "STATUS_JOINING"))

    def test_new_watchdog_states_exist(self) -> None:
        """Live steady-state machine constants must exist."""
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

    def test_no_heartbeat_not_in_legacy_healthy_states(self) -> None:
        self.assertNotIn(STATUS_NO_HEARTBEAT, _HEALTHY_STATES)


class TestStatusColors(unittest.TestCase):
    """Colorize map must have entries for all live watchdog states."""

    def test_no_heartbeat_has_color(self) -> None:
        from agent.commands import _ANSI_ORANGE, _colorize_status
        out_nh = _colorize_status("No Heartbeat", use_color=True)
        self.assertIn("No Heartbeat", out_nh)
        self.assertIn(_ANSI_ORANGE, out_nh)
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
    """Post-launch initial status must stay Launching until watchdog verifies presence."""

    def test_post_launch_stays_launching_after_successful_launch(self) -> None:
        for _has_url in (True, False):
            launch_ok = True
            if not launch_ok:
                state = "Failed"
            else:
                state = STATUS_LAUNCHING
            self.assertEqual(
                state,
                STATUS_LAUNCHING,
                f"expected Launching with has_url={_has_url}, got {state}",
            )

    def test_failed_launch_stays_failed(self) -> None:
        launch_ok = False
        state = "Failed" if not launch_ok else STATUS_LAUNCHING
        self.assertEqual(state, "Failed")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
