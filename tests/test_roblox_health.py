"""Tests for conservative disconnect signal mapping."""

import unittest
import unittest.mock

from agent.roblox_health import UnhealthyEvidence, analyze_disconnect_signals, categorize_unhealthy


class RobloxHealthTests(unittest.TestCase):
    def test_categorize_unknown_without_evidence(self):
        with unittest.mock.patch("agent.roblox_health.analyze_disconnect_signals", return_value=None):
            self.assertEqual(categorize_unhealthy(None, "com.roblox.client"), "unknown_unhealthy")

    def test_categorize_prefers_logcat_evidence(self):
        ev = UnhealthyEvidence("disconnected", "logcat", "lost connection")
        with unittest.mock.patch("agent.roblox_health.analyze_disconnect_signals", return_value=ev):
            self.assertEqual(categorize_unhealthy("process_missing", "com.roblox.client"), "disconnected")

    def test_analyze_returns_none_without_matches(self):
        with unittest.mock.patch("agent.roblox_health._pid_for_package", return_value=None), \
             unittest.mock.patch("agent.android.run_command") as rc:
            rc.return_value = unittest.mock.MagicMock(ok=True, stdout="")
            self.assertIsNone(analyze_disconnect_signals("com.roblox.client"))


if __name__ == "__main__":
    unittest.main()
