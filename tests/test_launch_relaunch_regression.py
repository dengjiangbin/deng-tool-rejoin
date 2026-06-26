"""Launch/relaunch regression + webhook wording tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import webhook
from agent.launch_relaunch_trace import record_launch_attempt, sanitized_url_from_context
from agent.lifecycle_reasons import format_user_friendly_dead_reason
from agent.rjn_lifecycle_monitor import RjnLifecycleMonitor, STATE_RELAUNCHING


class LaunchRelaunchRegressionTests(unittest.TestCase):
    def test_launch_records_private_url_masked(self) -> None:
        with patch("agent.launch_relaunch_trace._save"):
            record_launch_attempt(
                "com.test.pkg",
                action="launch_start",
                success=True,
                launcher="private_url",
                url_present=True,
                url_sanitized="https://www.roblox.com/...masked...",
                command_type="private_url",
            )

    def test_sanitized_url_from_context(self) -> None:
        present, masked = sanitized_url_from_context({
            "url_mode": "private_url",
            "effective_url": "https://www.roblox.com/games/1/x?privateServerLinkCode=secret",
        })
        self.assertTrue(present)
        self.assertNotIn("secret", masked)

    def test_relaunching_preserved_in_detection(self) -> None:
        mon = RjnLifecycleMonitor(["com.test.pkg"])
        mon._uid_map = {"com.test.pkg": "100"}
        mon.note_launch_watchdog("com.test.pkg", relaunch=True)
        self.assertTrue(mon._states["com.test.pkg"].relaunching)
        with patch.object(mon, "_process_check", return_value=(True, ["1"])):
            ev = mon.evaluate_package("com.test.pkg")
        self.assertEqual(ev.public_status, "Relaunching")
        self.assertEqual(mon._states["com.test.pkg"].internal_state, STATE_RELAUNCHING)

    def test_note_launch_watchdog_does_not_force_stop(self) -> None:
        with patch("agent.android.run_command") as rc, patch("agent.android.force_stop_package") as fs:
            mon = RjnLifecycleMonitor(["com.test.pkg"])
            mon.note_launch_watchdog("com.test.pkg", relaunch=False)
            fs.assert_not_called()
            rc.assert_not_called()

    def test_do_launch_uses_launch_package_for_current_config(self) -> None:
        from agent.supervisor import WatchdogSupervisor, STATUS_LAUNCHING

        entry = {"package": "com.test.pkg", "account_username": "u1"}
        sup = WatchdogSupervisor([entry], {"roblox_packages": [entry]})
        sup.status_map["com.test.pkg"] = STATUS_LAUNCHING
        with patch("agent.supervisor.launch_package_for_current_config") as launch, \
             patch("agent.supervisor.private_url_launch_context", return_value={
                 "url_mode": "private_url",
                 "url": "https://example.test/join?privateServerLinkCode=abc",
                 "private_url_mode": "global",
                 "url_config_source": "entry",
             }), \
             patch("agent.launch_relaunch_trace.record_launch_attempt"), \
             patch("agent.supervisor._reapply_layout_for_package"):
            launch.return_value = MagicMock(success=True, error="")
            ok = sup._do_launch("com.test.pkg", entry, "test")
        self.assertTrue(ok)
        launch.assert_called_once()

    def test_no_termux_exit_on_launch(self) -> None:
        with patch("os._exit") as oe, patch("sys.exit") as se:
            from agent.launch_relaunch_trace import record_launch_attempt

            record_launch_attempt(
                "com.test.pkg",
                action="launch_test",
                success=True,
                launcher="private_url",
                url_present=True,
            )
            oe.assert_not_called()
            se.assert_not_called()


class WebhookReasonTests(unittest.TestCase):
    def test_reason_field_label(self) -> None:
        payload = webhook.build_package_lifecycle_embed_payload(
            {"device_name": "Phone"},
            event="package_dead",
            package="com.test.pkg",
            username="User1",
            runtime_seconds=10.0,
            dead_reason="launch_watchdog_timeout",
        )
        names = [f["name"] for f in payload["embeds"][0]["fields"]]
        self.assertIn("Reason", names)
        self.assertNotIn("Dead Reason", names)

    def test_user_friendly_watchdog_timeout(self) -> None:
        text = format_user_friendly_dead_reason("launch_watchdog_timeout")
        self.assertEqual(text, "Roblox did not finish joining the server in time")
        self.assertNotIn("launch_watchdog", text)

    def test_user_friendly_process_missing(self) -> None:
        text = format_user_friendly_dead_reason("process_missing")
        self.assertIn("closed or force-stopped", text)


if __name__ == "__main__":
    unittest.main()
