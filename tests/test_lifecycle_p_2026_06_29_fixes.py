"""Regression tests for the 2026-06-29 lifecycle fixes.

1. Error code 529 (and any GL-rendered kick) is caught via in-game heartbeat
   loss: a package kept Online only by a now-silent detector.lua heartbeat is
   demoted to Disconnected so recovery triggers — unless a captcha overlay is up
   (hang, no recovery) or Roblox Presence independently confirms it's in-game.
2. "Account Recovered" only fires for a dead episode observed in THIS session,
   so stale persisted dead state never produces a spurious recovered on first
   launch.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import supervisor, webhook
from agent.rjn_lifecycle_monitor import PackageEvaluateResult

PKG = "com.roblox.client"
URL = "https://discord.com/api/webhooks/1234567890/secret-token"


def _cfg() -> dict:
    return {
        "webhook_mode": "new_post",
        "webhook_enabled": True,
        "webhook_url": URL,
        "device_name": "TestPhone",
        "roblox_packages": [{"package": PKG, "account_username": "MainUser"}],
    }


def _online_ev(source: str = "push_heartbeat", *, process: bool = True) -> PackageEvaluateResult:
    return PackageEvaluateResult(
        package=PKG,
        internal_state="ONLINE_CONFIRMED",
        public_status="Online",
        reason="online",
        is_online_confirmed=True,
        failed_checks=[],
        process_exists=process,
        detail={"online_evidence_source": source},
    )


def _disconnected_ev() -> PackageEvaluateResult:
    return PackageEvaluateResult(
        package=PKG,
        internal_state="DISCONNECTED",
        public_status="Disconnected",
        reason="heartbeat_lost",
        is_online_confirmed=False,
        failed_checks=["with_reason_after_join"],
        process_exists=True,
        detail={"reason_internal": "heartbeat_lost"},
    )


class _Presence:
    def __init__(self, in_game: bool) -> None:
        self.is_in_game = in_game


class HeartbeatLossDemotionTests(unittest.TestCase):
    def _sup(self) -> supervisor.WatchdogSupervisor:
        entry = {"package": PKG, "account_username": "MainUser"}
        sup = supervisor.WatchdogSupervisor([entry], _cfg())
        sup._push_ever_seen.add(PKG)
        sup._push_last_seen[PKG] = time.time() - 300.0  # well past the 90s grace
        return sup

    def test_stale_heartbeat_demotes_to_disconnect(self) -> None:
        sup = self._sup()
        with patch("agent.package_online_evidence.detect_live_captcha", return_value=None), \
             patch.object(sup, "_fetch_presence", return_value=None), \
             patch.object(sup._rjn_monitor, "apply_disconnect") as disc, \
             patch.object(sup._rjn_monitor, "evaluate_package", return_value=_disconnected_ev()):
            result = sup._maybe_demote_on_heartbeat_loss(PKG, _online_ev())
        disc.assert_called_once()
        self.assertEqual(disc.call_args.kwargs.get("reason"), "heartbeat_lost")
        self.assertFalse(result.is_online_confirmed)

    def test_fresh_heartbeat_is_not_demoted(self) -> None:
        sup = self._sup()
        sup._push_last_seen[PKG] = time.time()  # fresh
        with patch.object(sup._rjn_monitor, "apply_disconnect") as disc:
            ev = _online_ev()
            result = sup._maybe_demote_on_heartbeat_loss(PKG, ev)
        disc.assert_not_called()
        self.assertIs(result, ev)

    def test_detector_not_used_is_not_demoted(self) -> None:
        sup = self._sup()
        sup._push_ever_seen.discard(PKG)  # no detector for this package
        with patch.object(sup._rjn_monitor, "apply_disconnect") as disc:
            ev = _online_ev()
            result = sup._maybe_demote_on_heartbeat_loss(PKG, ev)
        disc.assert_not_called()
        self.assertIs(result, ev)

    def test_non_heartbeat_online_source_is_not_demoted(self) -> None:
        sup = self._sup()
        with patch.object(sup._rjn_monitor, "apply_disconnect") as disc:
            ev = _online_ev(source="gamejoinloadtime")
            result = sup._maybe_demote_on_heartbeat_loss(PKG, ev)
        disc.assert_not_called()
        self.assertIs(result, ev)

    def test_captcha_overlay_hangs_without_recovery(self) -> None:
        sup = self._sup()
        with patch(
            "agent.package_online_evidence.detect_live_captcha",
            return_value="Verifying you're not a bot",
        ), patch.object(sup._rjn_monitor, "apply_disconnect") as disc:
            result = sup._maybe_demote_on_heartbeat_loss(PKG, _online_ev())
        disc.assert_not_called()                       # captcha must NOT recover
        self.assertIn(PKG, sup._captcha_detected)
        self.assertFalse(result.is_online_confirmed)   # caller maps to STATUS_CAPTCHA

    def test_presence_in_game_keeps_online(self) -> None:
        sup = self._sup()
        with patch("agent.package_online_evidence.detect_live_captcha", return_value=None), \
             patch.object(sup, "_fetch_presence", return_value=_Presence(True)), \
             patch.object(sup._rjn_monitor, "apply_disconnect") as disc, \
             patch.object(sup._rjn_monitor, "confirm_online_evidence") as confirm, \
             patch.object(sup._rjn_monitor, "evaluate_package", return_value=_online_ev()):
            sup._maybe_demote_on_heartbeat_loss(PKG, _online_ev())
        disc.assert_not_called()                       # healthy client not relaunched
        confirm.assert_called_once()
        # grace reset so we don't re-check every round
        self.assertGreater(sup._push_last_seen[PKG], time.time() - 5)

    def test_offline_process_is_not_demoted_here(self) -> None:
        # Process gone is handled by the normal dead path, not heartbeat loss.
        sup = self._sup()
        with patch.object(sup._rjn_monitor, "apply_disconnect") as disc:
            ev = _online_ev(process=False)
            ev.is_online_confirmed = False
            result = sup._maybe_demote_on_heartbeat_loss(PKG, ev)
        disc.assert_not_called()
        self.assertIs(result, ev)


class RecoveredSessionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._state_path = webhook.DATA_DIR / "package-lifecycle-webhook-state.json"
        self._backup = self._state_path.read_text(encoding="utf-8") if self._state_path.is_file() else None
        self._state_path.unlink(missing_ok=True)

    def tearDown(self) -> None:
        self._state_path.unlink(missing_ok=True)
        if self._backup is not None:
            self._state_path.write_text(self._backup, encoding="utf-8")

    def _sup(self) -> supervisor.WatchdogSupervisor:
        entry = {"package": PKG, "account_username": "MainUser"}
        return supervisor.WatchdogSupervisor([entry], _cfg())

    def test_recovered_suppressed_for_stale_cross_session_dead(self) -> None:
        # Simulate persisted dead state from a PREVIOUS run (recover_pending True)
        # but no in-session dead webhook → recovered must NOT fire on first launch.
        webhook.arm_package_lifecycle_dead_episode(PKG)
        webhook.mark_package_lifecycle_dead_notified(PKG)
        self.assertTrue(webhook.package_lifecycle_recover_pending(PKG))
        sup = self._sup()
        self.assertNotIn(PKG, sup._session_dead_notified)
        with patch("agent.webhook.send_package_lifecycle_alert", return_value=(True, "ok")) as send:
            sup._maybe_send_package_recovered_webhook(PKG, {"package": PKG})
        send.assert_not_called()

    def test_dead_webhook_sets_session_marker_then_recovered_fires(self) -> None:
        entry = {"package": PKG, "account_username": "MainUser"}
        sup = self._sup()
        sup._last_online_ts[PKG] = 1.0
        with patch.object(sup, "_in_loading_grace", return_value=False), \
             patch.object(sup, "_in_grace", return_value=False), \
             patch("agent.webhook.send_package_lifecycle_alert", return_value=(True, "ok")):
            sup._maybe_send_package_dead_webhook(
                PKG, entry, supervisor.STATUS_ONLINE, supervisor.STATUS_DEAD, 0.0
            )
        self.assertIn(PKG, sup._session_dead_notified)
        self.assertTrue(webhook.package_lifecycle_recover_pending(PKG))
        with patch("agent.webhook.send_package_lifecycle_alert", return_value=(True, "ok")) as send:
            sup._maybe_send_package_recovered_webhook(PKG, entry)
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs.get("event"), "package_recovered")
        self.assertNotIn(PKG, sup._session_dead_notified)  # cleared after recovered


if __name__ == "__main__":
    unittest.main()
