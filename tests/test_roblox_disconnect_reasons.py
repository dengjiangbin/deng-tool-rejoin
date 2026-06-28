"""Tests for Roblox disconnect error-code Reason formatting."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.roblox_disconnect_reasons import format_error_code_reason, format_lifecycle_dead_reason


class RobloxDisconnectReasonTests(unittest.TestCase):
    def test_idle_278_from_prompt_text(self) -> None:
        text = "Error Code: 278 You were disconnected for being idle 20 minutes"
        self.assertEqual(
            format_error_code_reason(text),
            "Error Code: 278 You were disconnected for being idle 20 minutes",
        )

    def test_lifecycle_reason_prefers_error_code(self) -> None:
        reason = format_lifecycle_dead_reason(
            "idle_disconnect_278",
            "Error Code: 278 You were disconnected for being idle",
        )
        self.assertTrue(reason.startswith("Error Code: 278"))

    def test_fallback_without_code(self) -> None:
        reason = format_lifecycle_dead_reason("process_missing", None)
        self.assertIn("closed", reason.lower())


if __name__ == "__main__":
    unittest.main()
