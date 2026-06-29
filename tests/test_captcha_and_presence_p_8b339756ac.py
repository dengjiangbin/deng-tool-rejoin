"""Regression tests for probe p-8b339756ac.

1. Captcha shown but no Account Dead webhook: a captcha overlay emits no
   disconnect line, so a stale gamejoinloadtime kept the package "Online" and
   the old code cleared the captcha flag before it could become STATUS_CAPTCHA.
   STATUS_CAPTCHA must now take precedence over a stale online marker and stay
   sticky until a genuinely newer in-game proof arrives (or the process dies).
2. Detection speed: the rate-limited Roblox Presence API must be throttled to a
   safety-net cadence while the real-time logcat stream is fresh, instead of
   running per-package-per-round.
"""

from __future__ import annotations

import sys
import time
import types
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import supervisor
from agent.rjn_lifecycle_monitor import PackageRjnState

PKG = "com.roblox.client"


def _sup() -> supervisor.WatchdogSupervisor:
    entry = {"package": PKG, "account_username": "U"}
    return supervisor.WatchdogSupervisor([entry], {"roblox_packages": [entry]})


def _ev(*, process: bool = True, online: bool = True):
    return types.SimpleNamespace(process_exists=process, is_online_confirmed=online)


class CaptchaStickyTests(unittest.TestCase):
    def _row(self, last_online_at: float) -> None:
        row = PackageRjnState(package=PKG)
        row.last_positive_online_evidence_at = last_online_at
        self.sup._rjn_monitor._states[PKG] = row

    def setUp(self) -> None:
        self.sup = _sup()

    def test_captcha_held_over_stale_online(self) -> None:
        # Captcha first seen now; the only online proof is OLDER (stale).
        now = time.time()
        self.sup._captcha_detected[PKG] = "Verifying you're not a bot"
        self.sup._captcha_detected_at[PKG] = now
        self._row(last_online_at=now - 120.0)  # stale gamejoinloadtime
        self.assertTrue(self.sup._captcha_state_held(PKG, _ev(online=True)))

    def test_captcha_cleared_when_newer_online_proof(self) -> None:
        now = time.time()
        self.sup._captcha_detected[PKG] = "captcha"
        self.sup._captcha_detected_at[PKG] = now
        self._row(last_online_at=now + 5.0)  # solved + rejoined → newer proof
        self.assertFalse(self.sup._captcha_state_held(PKG, _ev(online=True)))
        self.assertNotIn(PKG, self.sup._captcha_detected)

    def test_captcha_cleared_when_process_gone(self) -> None:
        self.sup._captcha_detected[PKG] = "captcha"
        self.sup._captcha_detected_at[PKG] = time.time()
        self._row(last_online_at=0.0)
        self.assertFalse(self.sup._captcha_state_held(PKG, _ev(process=False)))
        self.assertNotIn(PKG, self.sup._captcha_detected)

    def test_no_flag_means_not_held(self) -> None:
        self.assertFalse(self.sup._captcha_state_held(PKG, _ev()))

    def test_captcha_state_is_a_dead_webhook_state(self) -> None:
        # The webhook must be allowed to fire for the captcha hang.
        self.assertIn(supervisor.STATUS_CAPTCHA, supervisor._ACCOUNT_DEAD_WEBHOOK_STATES)
        # ...but recovery must NOT fire for it (it hangs for a human).
        self.assertNotIn(supervisor.STATUS_CAPTCHA, supervisor._RECOVERY_TRIGGER_STATES)


class PresenceThrottleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sup = _sup()

    def test_presence_skipped_while_stream_fresh_and_recent(self) -> None:
        self.sup._rjn_monitor.stream_fresh_for = lambda pkg, age: True  # type: ignore[assignment]
        self.assertTrue(self.sup._presence_verify_due(PKG))   # first call: due
        self.assertFalse(self.sup._presence_verify_due(PKG))  # immediately after: throttled

    def test_presence_runs_when_stream_stale(self) -> None:
        self.sup._rjn_monitor.stream_fresh_for = lambda pkg, age: False  # type: ignore[assignment]
        self.assertTrue(self.sup._presence_verify_due(PKG))
        self.assertTrue(self.sup._presence_verify_due(PKG))   # stale → every round

    def test_presence_due_again_after_interval(self) -> None:
        self.sup._rjn_monitor.stream_fresh_for = lambda pkg, age: True  # type: ignore[assignment]
        self.assertTrue(self.sup._presence_verify_due(PKG))
        # Backdate the last verify beyond the interval → due again.
        self.sup._last_presence_verify_at[PKG] = (
            time.monotonic() - self.sup.PRESENCE_VERIFY_INTERVAL_SECONDS - 1.0
        )
        self.assertTrue(self.sup._presence_verify_due(PKG))


if __name__ == "__main__":
    unittest.main()
