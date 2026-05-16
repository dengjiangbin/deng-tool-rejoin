"""Tests for agent.playing_state — heartbeat-based playing-state detection.

These tests intentionally don't touch any device.  Every test feeds
synthetic evidence dicts (the same shape ``android.get_package_alive_evidence``
returns) and asserts the public state label.
"""

from __future__ import annotations

import unittest

from agent.playing_state import (
    STATE_BACKGROUND,
    STATE_FAILED,
    STATE_JOIN_UNCONFIRMED,
    STATE_LOBBY,
    STATE_OFFLINE,
    STATE_ONLINE,
    STATE_PLAYING,
    STATE_RECOVERING,
    StateTracker,
    classify_ui_signal,
)


def _ev(**kwargs) -> dict[str, bool]:
    base = {
        "running": False, "root_running": False, "task": False,
        "window": False, "surface": False, "foreground": False,
    }
    base.update(kwargs)
    return base


class TestClassifyUiSignal(unittest.TestCase):
    def test_in_game_tokens_detected(self) -> None:
        text = "<node text='Leave Game'/><node text='Reset Character'/>"
        self.assertEqual(classify_ui_signal(text), "in_game")

    def test_lobby_tokens_detected(self) -> None:
        text = "<node text='Recommended For You'/><node text='Add Friends'/>"
        self.assertEqual(classify_ui_signal(text), "lobby")

    def test_empty_returns_unknown(self) -> None:
        self.assertEqual(classify_ui_signal(""), "unknown")
        self.assertEqual(classify_ui_signal(None), "unknown")

    def test_no_tokens_returns_unknown(self) -> None:
        self.assertEqual(classify_ui_signal("random text"), "unknown")


class TestPlayingStateRules(unittest.TestCase):
    """The core "visible in-game must not be Offline" guarantees."""

    def setUp(self) -> None:
        self.tracker = StateTracker(offline_grace_s=60.0)

    def test_visible_in_game_ui_is_playing(self) -> None:
        d = self.tracker.decide(
            "pkg", "Online",
            _ev(running=True, window=True),
            ui_signal="in_game",
        )
        self.assertEqual(d.state, STATE_PLAYING)

    def test_visible_surface_alone_is_online_not_offline(self) -> None:
        """The cloud-phone screenshot case: clone is rendering but pidof can't see it."""
        d = self.tracker.decide(
            "pkg", "Online",
            _ev(running=False, window=False, surface=True),
        )
        self.assertEqual(d.state, STATE_ONLINE)
        self.assertNotEqual(d.state, STATE_OFFLINE)

    def test_visible_window_alone_is_online(self) -> None:
        d = self.tracker.decide(
            "pkg", "Online",
            _ev(window=True),
        )
        self.assertEqual(d.state, STATE_ONLINE)

    def test_foreground_alone_is_online(self) -> None:
        d = self.tracker.decide(
            "pkg", "Online",
            _ev(foreground=True),
        )
        self.assertEqual(d.state, STATE_ONLINE)

    def test_process_plus_visual_is_online(self) -> None:
        d = self.tracker.decide(
            "pkg", "Online",
            _ev(running=True, window=True),
        )
        self.assertEqual(d.state, STATE_ONLINE)

    def test_lobby_ui_is_lobby_not_offline(self) -> None:
        d = self.tracker.decide(
            "pkg", "Online",
            _ev(running=True, window=True),
            ui_signal="lobby",
        )
        self.assertEqual(d.state, STATE_LOBBY)

    def test_weak_url_evidence_is_join_unconfirmed(self) -> None:
        d = self.tracker.decide(
            "pkg", "Joining",
            _ev(task=True),
            url_launched=True,
        )
        self.assertEqual(d.state, STATE_JOIN_UNCONFIRMED)

    def test_process_only_is_background(self) -> None:
        d = self.tracker.decide(
            "pkg", "Online",
            _ev(running=True),
        )
        self.assertEqual(d.state, STATE_BACKGROUND)

    def test_stale_task_only_within_grace_is_background(self) -> None:
        # First observation: task only.
        self.tracker.observe("pkg", _ev(task=True), now=100.0)
        d = self.tracker.decide(
            "pkg", "Online",
            _ev(task=True),
            now=105.0,   # only 5s after — still within stale_task_grace_s=20
        )
        # Should be Background (waiting), not Offline.
        self.assertEqual(d.state, STATE_BACKGROUND)

    def test_no_evidence_within_grace_is_recovering(self) -> None:
        # Establish recent heartbeat then go dark.
        self.tracker.observe("pkg", _ev(running=True, window=True), now=100.0)
        d = self.tracker.decide(
            "pkg", "Online",
            _ev(),                          # nothing
            now=130.0,                      # 30s gap — within 60s grace
        )
        self.assertEqual(d.state, STATE_RECOVERING)

    def test_no_evidence_beyond_grace_is_offline(self) -> None:
        self.tracker.observe("pkg", _ev(running=True, window=True), now=100.0)
        d = self.tracker.decide(
            "pkg", "Online",
            _ev(),
            now=200.0,                      # 100s gap — well past 60s grace
        )
        self.assertEqual(d.state, STATE_OFFLINE)

    def test_never_observed_is_offline(self) -> None:
        d = self.tracker.decide("pkg", None, _ev())
        self.assertEqual(d.state, STATE_OFFLINE)

    def test_failed_state_after_max_attempts(self) -> None:
        self.tracker.observe("pkg", _ev(running=True), now=100.0)
        d = self.tracker.decide(
            "pkg", "Reconnecting",
            _ev(),
            attempt_count=5,
            max_attempts=5,
            now=300.0,                      # past grace
        )
        self.assertEqual(d.state, STATE_FAILED)


class TestObserveUpdatesTimestamps(unittest.TestCase):
    def test_observe_records_heartbeats_per_source(self) -> None:
        t = StateTracker()
        hb = t.observe("p", _ev(running=True, window=True), now=10.0)
        self.assertEqual(hb.last_process, 10.0)
        self.assertEqual(hb.last_window, 10.0)
        self.assertEqual(hb.last_surface, 0.0)

    def test_reset_clears_heartbeat(self) -> None:
        t = StateTracker()
        t.observe("p", _ev(running=True), now=10.0)
        t.reset("p")
        # decide with no fresh evidence on a reset tracker → Offline (never observed).
        d = t.decide("p", None, _ev())
        self.assertEqual(d.state, STATE_OFFLINE)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
