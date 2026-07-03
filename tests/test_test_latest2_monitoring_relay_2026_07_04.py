"""Central Monitoring relay for test/latest2."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.test_latest2_monitoring_relay import (  # noqa: E402
    MonitoringRelay,
    PRESENCE_DEAD,
    PRESENCE_ONLINE,
    RELAY_VERSION,
    commit_presence_state,
    submit_raw_evidence,
)


class MonitoringRelayTests(unittest.TestCase):
    def test_commit_is_only_presence_writer(self) -> None:
        sup = MagicMock()
        sup.status_map = {}
        sup._render_callback = None
        relay = MonitoringRelay(packages=["com.moons.litesc"])
        relay.bind_supervisor(sup, direct_set_status=MagicMock())
        with patch("agent.lime_channel.lime_detection_enabled", return_value=True):
            ok = relay.commit_presence_state(
                "com.moons.litesc",
                PRESENCE_ONLINE,
                source="heartbeat",
                writer="monitoring_relay",
                trigger_recovery=False,
            )
        self.assertTrue(ok)
        self.assertEqual(relay.committed_presence_state("com.moons.litesc"), PRESENCE_ONLINE)

    def test_submit_raw_does_not_commit(self) -> None:
        relay = MonitoringRelay(packages=["com.moons.litesc"])
        relay.submit_raw_evidence(
            "com.moons.litesc",
            hint="dead",
            source="process",
            evidence="process_missing",
            process_exists=False,
        )
        self.assertEqual(relay.committed_presence_state("com.moons.litesc"), "")
        with relay._lock:
            row = relay._rows["com.moons.litesc"]
            self.assertTrue(row.raw_dead_pending)

    def test_dead_commit_triggers_recovery_thread(self) -> None:
        sup = MagicMock()
        sup.status_map = {"com.moons.litesc": "Launching"}
        relay = MonitoringRelay(packages=["com.moons.litesc"])
        relay.bind_supervisor(
            sup,
            entries={"com.moons.litesc": {}},
            direct_set_status=MagicMock(),
        )
        with patch("agent.lime_channel.lime_detection_enabled", return_value=True):
            with patch.object(relay, "_trigger_immediate_recovery") as trig:
                relay.commit_presence_state(
                    "com.moons.litesc",
                    PRESENCE_DEAD,
                    source="process",
                    evidence="process_missing",
                )
                trig.assert_called_once()

    def test_module_helpers_without_active_relay(self) -> None:
        with patch("agent.test_latest2_monitoring_relay.get_active_relay", return_value=None):
            self.assertFalse(commit_presence_state("com.moons.litesc", PRESENCE_DEAD))
            submit_raw_evidence("com.moons.litesc", hint="dead", source="process")


class PackageDiscoveryTests(unittest.TestCase):
    def test_probe_snapshot_shape(self) -> None:
        from agent.lime_package_discovery import probe_package_discovery_snapshot

        snap = probe_package_discovery_snapshot()
        self.assertIn("packages", snap)
        self.assertIn("package_count", snap)
        self.assertIn("duration_ms", snap)


class RuntimePatchMonitoringTests(unittest.TestCase):
    def test_monitoring_relay_patch_registered(self) -> None:
        from agent.test_latest2_runtime_patch import apply_test_latest2_runtime_patches

        with patch("agent.lime_channel.lime_detection_enabled", return_value=True):
            apply_test_latest2_runtime_patches()
            from agent import supervisor as sup

            self.assertTrue(
                getattr(sup.WatchdogSupervisor, "_test_latest2_monitoring_relay_patched", False)
            )


class RelayVersionTests(unittest.TestCase):
    def test_relay_version_constant(self) -> None:
        self.assertEqual(RELAY_VERSION, "test-latest2-monitoring-v1")


if __name__ == "__main__":
    unittest.main()
