"""Regression tests for probe p-1bc476d931 fixes.

Covers the user-reported items:
  4. ALL Roblox error codes are treated as a real disconnect (not only 278) —
     e.g. 529 (HTTP error), 524 (timeout), 517.
  5. Captcha / bot-verification is detected as a distinct Captcha state that
     fires an "Account Dead" webhook (reason "Captcha Verification") but is
     EXCLUDED from the recovery system (the package hangs for a human).
  6. Wrong-server detection also covers a change of joined server instance
     (jobId / gameId) — "same game, different server".
  7/8. Non-blocking recovery still promotes Relaunching→Online and now fires the
     "Account Recovered" webhook from the steady-Online branch.
"""

from __future__ import annotations

import inspect
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.constants import DATA_DIR
from agent.rjn_lifecycle_monitor import (
    RjnLifecycleMonitor,
    STATE_DISCONNECTED,
    STATE_ONLINE_CONFIRMED,
)
from agent.roblox_disconnect_reasons import (
    format_lifecycle_dead_reason,
    internal_reason_for_disconnect_code,
)
from agent import package_online_evidence as poe


def _online_monitor(pkg: str, uid: str) -> RjnLifecycleMonitor:
    mon = RjnLifecycleMonitor([pkg])
    mon._uid_map = {pkg: uid}
    mon._uid_to_package = {uid: pkg}
    mon._monitor_started_at = time.time() - 60
    row = mon._states[pkg]
    row.uid = uid
    row.internal_state = STATE_ONLINE_CONFIRMED
    row.last_positive_online_evidence_at = time.time() - 30
    row.last_gamejoinloadtime_at = time.time() - 30
    row.online_since = time.time() - 30
    row.online_evidence_source = "gamejoinloadtime"
    return mon


class AllErrorCodesAreDeadTests(unittest.TestCase):
    def setUp(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def tearDown(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def test_code_mapping_covers_full_range(self) -> None:
        self.assertEqual(internal_reason_for_disconnect_code(529), "disconnect_code_529")
        self.assertEqual(internal_reason_for_disconnect_code(524), "disconnect_code_524")
        self.assertEqual(internal_reason_for_disconnect_code(517), "disconnect_code_517")
        # 278 keeps its dedicated idle key.
        self.assertEqual(internal_reason_for_disconnect_code(278), "idle_disconnect_278")

    def test_529_reason_text_is_clean(self) -> None:
        text = format_lifecycle_dead_reason(
            "disconnect_code_529",
            "A Http error has occurred. Please close the client and try again. (Error Code: 529)",
        )
        self.assertIn("529", text)
        self.assertIn("Http error", text)
        # No doubled "Error Code: 529 Error Code: 529".
        self.assertEqual(text.count("Error Code: 529"), 1)

    def test_logcat_error_code_529_line_flags_disconnect(self) -> None:
        pkg = "com.pkg.code529"
        mon = _online_monitor(pkg, "20101")
        line = (
            "uid=20101 I Roblox  : [FLog::Output] A Http error has occurred. "
            "Please close the client and try again. (Error Code: 529)"
        )
        mon._handle_logcat_line(line)
        with patch.object(mon, "_process_check", return_value=(True, ["1"])):
            ev = mon.evaluate_package(pkg)
        row = mon._states[pkg]
        self.assertEqual(ev.internal_state, STATE_DISCONNECTED)
        self.assertFalse(ev.is_online_confirmed)
        self.assertEqual(row.last_disconnect_code, 529)

    def test_ui_scan_matches_generic_error_code_and_http(self) -> None:
        found, _ = poe._scan_disconnect_text("Something Error Code: 529 happened")
        self.assertTrue(found)
        found2, _ = poe._scan_disconnect_text("A Http error has occurred")
        self.assertTrue(found2)


class CaptchaDetectionTests(unittest.TestCase):
    def test_captcha_patterns_match_screen_text(self) -> None:
        for blob in (
            "Verifying you're not a bot",
            "Please solve this challenge so we know you are a real person",
            "Start Puzzle",
        ):
            found, text = poe._scan_captcha_text(blob)
            self.assertTrue(found, blob)
            self.assertTrue(text)

    def test_captcha_text_is_not_a_normal_disconnect_word(self) -> None:
        # "not a bot" must not also match the disconnect patterns (so it is routed
        # to the Captcha state, not recovery).
        found, _ = poe._scan_disconnect_text("Verifying you're not a bot")
        self.assertFalse(found)

    def test_detect_live_captcha_uses_ui_dump(self) -> None:
        class _Res:
            ok = True
            stdout = "<node text='Verifying you\\'re not a bot'/>"

        with patch.object(poe.android, "run_command", return_value=_Res()):
            self.assertIsNotNone(poe.detect_live_captcha("com.pkg.captcha"))

    def test_captcha_reason_text(self) -> None:
        self.assertEqual(
            format_lifecycle_dead_reason("captcha_verification", None),
            "Captcha Verification",
        )


class CaptchaStateWiringTests(unittest.TestCase):
    def test_captcha_state_fires_webhook_but_not_recovery(self) -> None:
        from agent import supervisor as sv

        self.assertIn(sv.STATUS_CAPTCHA, sv._ACCOUNT_DEAD_WEBHOOK_STATES)
        self.assertNotIn(sv.STATUS_CAPTCHA, sv._RECOVERY_TRIGGER_STATES)
        # Captcha is its own public state, distinct from Dead/Disconnected.
        self.assertEqual(sv.STATUS_CAPTCHA, "Captcha")

    def test_detect_state_has_captcha_branch(self) -> None:
        from agent import supervisor as sv

        src = inspect.getsource(sv.WatchdogSupervisor._detect_android_package_state)
        self.assertIn("STATUS_CAPTCHA", src)
        self.assertIn("_captcha_detected", src)

    def test_recovered_webhook_called_from_online_branch(self) -> None:
        from agent import supervisor as sv

        src = inspect.getsource(sv.WatchdogSupervisor._handle_state)
        self.assertIn("_maybe_send_package_recovered_webhook", src)


class WrongServerJobIdTests(unittest.TestCase):
    def setUp(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def tearDown(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    _GUID_A = "11111111-2222-3333-4444-555555555555"
    _GUID_B = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def test_server_instance_change_flags_wrong_server(self) -> None:
        pkg = "com.pkg.jobchange"
        mon = _online_monitor(pkg, "20201")
        # Private-server config (Server share) — server-instance must stay pinned.
        mon.set_expected_target(pkg, private_code="secretcode", share_type="Server")
        first = (
            f"uid=20201 I Roblox  : [FLog::GameJoinUtil] JoinGameNow gameId:{self._GUID_A}"
        )
        mon._handle_logcat_line(first)
        # First server anchors; must NOT flag yet.
        self.assertNotEqual(mon._states[pkg].last_transition_reason, "wrong_server")
        second = (
            f"uid=20201 I Roblox  : [FLog::GameJoinUtil] JoinGameNow gameId:{self._GUID_B}"
        )
        mon._handle_logcat_line(second)
        with patch.object(mon, "_process_check", return_value=(True, ["1"])):
            ev = mon.evaluate_package(pkg)
        row = mon._states[pkg]
        self.assertEqual(row.last_transition_reason, "wrong_server")
        self.assertEqual(ev.internal_state, STATE_DISCONNECTED)

    def test_same_server_instance_stays_online(self) -> None:
        pkg = "com.pkg.samejob"
        mon = _online_monitor(pkg, "20202")
        mon.set_expected_target(pkg, private_code="secretcode", share_type="Server")
        line = (
            f"uid=20202 I Roblox  : [FLog::GameJoinUtil] JoinGameNow gameId:{self._GUID_A}"
        )
        mon._handle_logcat_line(line)
        mon._handle_logcat_line(line)
        self.assertNotEqual(mon._states[pkg].last_transition_reason, "wrong_server")


if __name__ == "__main__":
    unittest.main()
