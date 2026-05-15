"""Health / disconnect wiring tests."""

import unittest
import unittest.mock

from agent.config import default_config, validate_config
from agent.monitor import check_package_health


class MonitorDisconnectTests(unittest.TestCase):
    def test_foreground_disconnect_evidence_marks_unhealthy(self):
        cfg = validate_config(default_config())

        def fake_fg():
            return "com.roblox.client"

        ev = unittest.mock.MagicMock()
        ev.category = "disconnected"
        ev.source = "logcat"
        with unittest.mock.patch("agent.android.network_available", return_value=True), \
             unittest.mock.patch("agent.android.package_installed", return_value=True), \
             unittest.mock.patch("agent.android.is_process_running", return_value=True), \
             unittest.mock.patch("agent.android.current_foreground_package", side_effect=fake_fg), \
             unittest.mock.patch("agent.roblox_health.analyze_disconnect_signals", return_value=ev):
            h = check_package_health(cfg, "com.roblox.client")
        self.assertEqual(h.state, "roblox_not_running")
        self.assertEqual(h.meta.get("disconnect_category"), "disconnected")


if __name__ == "__main__":
    unittest.main()
