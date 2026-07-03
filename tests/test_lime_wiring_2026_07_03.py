"""Lime fast-path wiring: force_close_race + checking online evidence."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

PKG = "com.moons.litesc"


class ForceCloseLimeWiringTests(unittest.TestCase):
    def test_process_poll_notifies_lime(self) -> None:
        from agent.force_close_race import ForceCloseRaceDetector, _notify_lime_process_dead

        mock_lime = MagicMock()
        with patch("agent.lime_detection_speed.get_active_lime_tracker", return_value=mock_lime):
            _notify_lime_process_dead(PKG, 1000.5)
        mock_lime.note_process_dead.assert_called_once_with(PKG, at=1000.5)

    def test_logcat_crash_notifies_lime(self) -> None:
        from agent.force_close_race import _notify_lime_logcat_dead

        mock_lime = MagicMock()
        with patch("agent.lime_detection_speed.get_active_lime_tracker", return_value=mock_lime):
            _notify_lime_logcat_dead(PKG, 1001.0, "force_stop")
        mock_lime.note_logcat_dead.assert_called_once_with(PKG, at=1001.0, evidence="force_stop")


class CheckingOnlineLimeWiringTests(unittest.TestCase):
    def test_set_online_evidence_notifies_lime(self) -> None:
        from agent import checker_pointer as cp

        ptr = cp.CheckerPointerState(session_id="t")
        mock_lime = MagicMock()
        with patch("agent.lime_detection_speed.get_active_lime_tracker", return_value=mock_lime):
            ptr.set_online_evidence(PKG, "gamejoinloadtime", 120.0)
        mock_lime.note_online_evidence.assert_called_once()
        args, kwargs = mock_lime.note_online_evidence.call_args
        self.assertEqual(args[0], PKG)
        self.assertEqual(kwargs.get("source"), "gamejoinloadtime")


if __name__ == "__main__":
    unittest.main()
