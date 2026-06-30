"""Regression for probe p-ac5a0399fc: No Heartbeat keeps runtime; Dead-only reset."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import status_monitor_runtime as smr
from agent.commands import _ANSI_ORANGE, _colorize_status
from agent.supervisor import (
    STATUS_DEAD,
    STATUS_NO_HEARTBEAT,
    STATUS_ONLINE,
    WatchdogSupervisor,
)


class NoHeartbeatRuntimeTests(unittest.TestCase):
    def _state_path(self, tmp: str) -> Path:
        return Path(tmp) / "status-monitor-runtime-state.json"

    def test_no_heartbeat_uses_orange_not_yellow(self) -> None:
        out = _colorize_status("No Heartbeat", use_color=True)
        self.assertIn(_ANSI_ORANGE, out)
        self.assertIn("No Heartbeat", out)

    def test_runtime_keeps_counting_during_no_heartbeat(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = self._state_path(tmp)
            with patch.object(smr, "_STATE_PATH", state_path):
                base = time.time() - 120.0
                smr.mark_online_confirmed_gamejoin("com.test.a", base)
                smr.mark_no_heartbeat("com.test.a", base + 120.0)
                self.assertAlmostEqual(
                    smr.effective_runtime_seconds("com.test.a", base + 180.0) or 0.0,
                    180.0,
                    delta=0.5,
                )

    def test_mark_no_heartbeat_preserves_online_since(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = self._state_path(tmp)
            with patch.object(smr, "_STATE_PATH", state_path):
                base = time.time() - 60.0
                smr.mark_online_confirmed_gamejoin("com.test.b", base)
                smr.mark_no_heartbeat("com.test.b", time.time())
                online_since, row = smr.load_online_since("com.test.b")
                self.assertAlmostEqual(float(online_since or 0.0), base, delta=0.5)
                self.assertEqual(row.get("state"), "NO_HEARTBEAT")

    def test_dead_clears_runtime(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = self._state_path(tmp)
            with patch.object(smr, "_STATE_PATH", state_path):
                now = time.time()
                smr.mark_online_confirmed_gamejoin("com.test.c", now - 60.0)
                smr.mark_no_heartbeat("com.test.c", now)
                smr.clear_online_since("com.test.c")
                self.assertIsNone(smr.effective_runtime_seconds("com.test.c", now))

    def test_supervisor_nhb_kill_switch_default_five_minutes(self) -> None:
        sup = WatchdogSupervisor([{"package": "com.test.d", "enabled": True}], {})
        self.assertEqual(sup.NHB_KILL_SWITCH_SECONDS, 300)

    def test_no_heartbeat_not_in_dead_webhook_states(self) -> None:
        from agent.supervisor import _ACCOUNT_DEAD_WEBHOOK_STATES

        self.assertNotIn(STATUS_NO_HEARTBEAT, _ACCOUNT_DEAD_WEBHOOK_STATES)
        self.assertIn(STATUS_DEAD, _ACCOUNT_DEAD_WEBHOOK_STATES)

    def test_record_runtime_keeps_anchor_on_no_heartbeat(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = self._state_path(tmp)
            with patch.object(smr, "_STATE_PATH", state_path):
                sup = WatchdogSupervisor([{"package": "com.test.e", "enabled": True}], {})
                now = time.time()
                smr.mark_online_confirmed_gamejoin("com.test.e", now - 90.0)
                sup._record_runtime_session_state(
                    "com.test.e", STATUS_ONLINE, STATUS_NO_HEARTBEAT, now
                )
                self.assertIn("com.test.e", sup._online_start_ts)
                self.assertAlmostEqual(
                    smr.effective_runtime_seconds("com.test.e", now + 30.0) or 0.0,
                    120.0,
                    delta=1.5,
                )

    def test_record_runtime_clears_only_on_dead(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = self._state_path(tmp)
            with patch.object(smr, "_STATE_PATH", state_path):
                sup = WatchdogSupervisor([{"package": "com.test.f", "enabled": True}], {})
                now = time.time()
                smr.mark_online_confirmed_gamejoin("com.test.f", now - 30.0)
                sup._record_runtime_session_state(
                    "com.test.f", STATUS_ONLINE, STATUS_DEAD, now
                )
                self.assertIsNone(smr.effective_runtime_seconds("com.test.f", now))


if __name__ == "__main__":
    unittest.main()
