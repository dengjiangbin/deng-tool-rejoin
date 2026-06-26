"""Per-package dead detection: 9 alive / 1 killed must mark only that package dead."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import webhook
from agent.android_logcat_detector import LogcatPackageEvent, poll_logcat_events
from agent.package_state_detector import PackageStateDetector, PROCESS_MISSING_CONFIRM_CHECKS


def _nine_packages() -> list[str]:
    return [f"com.moons.lites{i}" for i in range(9)]


class PackageDeadNineOneTests(unittest.TestCase):
    def test_kill_one_of_nine_marks_only_that_package_dead(self) -> None:
        packages = _nine_packages()
        killed = packages[3]
        alive = {pkg: True for pkg in packages}

        def process_alive(package: str) -> tuple[bool, str]:
            return alive.get(package, False), "1234" if alive.get(package) else ""

        detector = PackageStateDetector(packages)
        with patch.object(detector, "_process_alive", side_effect=process_alive), \
             patch.object(detector, "_sample_ram", return_value=("128", "proc_status", {"source": "proc_status", "fake": False})):
            for pkg in packages:
                result = detector.check_package(pkg, current_status="Online", was_metric_active=True)
                self.assertTrue(result.process_alive, pkg)
                self.assertFalse(result.confirmed_dead, pkg)

            alive[killed] = False
            pending = detector.check_package(killed, current_status="Online", was_metric_active=True)
            self.assertFalse(pending.process_alive)
            self.assertFalse(pending.confirmed_dead)
            self.assertEqual(pending.dead_reason, "process_missing_pending")

            confirmed = detector.check_package(killed, current_status="Online", was_metric_active=True)
            self.assertTrue(confirmed.confirmed_dead)
            self.assertEqual(confirmed.dead_reason, "process_missing")

            for pkg in packages:
                if pkg == killed:
                    continue
                other = detector.check_package(pkg, current_status="Online", was_metric_active=True)
                self.assertTrue(other.process_alive, pkg)
                self.assertFalse(other.confirmed_dead, pkg)

    def test_ram_unavailable_still_detects_dead_by_process(self) -> None:
        packages = ["com.moons.litesa", "com.moons.litesb"]
        alive = {"com.moons.litesa": True, "com.moons.litesb": True}

        def process_alive(package: str) -> tuple[bool, str]:
            return alive.get(package, False), ""

        detector = PackageStateDetector(packages)
        with patch.object(detector, "_process_alive", side_effect=process_alive), \
             patch.object(detector, "_sample_ram", return_value=("N/A", "unavailable", {"source": "unavailable", "fake": False})):
            for pkg in packages:
                detector.check_package(pkg, current_status="Online", was_metric_active=True)

            alive["com.moons.litesa"] = False
            detector.check_package("com.moons.litesa", current_status="Online", was_metric_active=True)
            dead = detector.check_package("com.moons.litesa", current_status="Online", was_metric_active=True)
            self.assertTrue(dead.confirmed_dead)
            self.assertEqual(dead.ram_display, "N/A")
            self.assertEqual(dead.ram_source, "unavailable")

            alive_ok = detector.check_package("com.moons.litesb", current_status="Online", was_metric_active=True)
            self.assertTrue(alive_ok.process_alive)

    def test_ram_string_one_point_two_gb_does_not_crash_embed(self) -> None:
        payload = webhook.build_package_lifecycle_embed_payload(
            {"device_name": "TestPhone"},
            event="package_dead",
            package="com.moons.litesa",
            username="UserA",
            runtime_seconds=30.0,
            dead_reason="process_missing",
            ram_display="1.2 GB",
        )
        blob = json.dumps(payload)
        self.assertIn("1.2 GB", blob)
        names = [f["name"] for f in payload["embeds"][0]["fields"]]
        self.assertIn("RAM", names)

    def test_logcat_gamejoinloadtime_clears_dead_for_one_package(self) -> None:
        import time

        from agent.android_logcat_detector import LogcatDetectorState

        packages = ["com.moons.litesa", "com.moons.litesb"]
        detector = PackageStateDetector(packages)

        with patch.object(detector, "_process_alive", return_value=(False, "")), \
             patch.object(detector, "_sample_ram", return_value=("N/A", "none", {})):
            detector.check_package("com.moons.litesa", current_status="Online", was_metric_active=True)
            dead = detector.check_package("com.moons.litesa", current_status="Online", was_metric_active=True)
            self.assertTrue(dead.confirmed_dead)

        with patch(
            "agent.package_state_detector.poll_logcat_events",
            return_value=(
                [
                    LogcatPackageEvent(
                        "com.moons.litesa",
                        "package_logcat_game_join_loaded",
                        "uid=10101 gamejoinloadtime",
                        time.time(),
                    ),
                ],
                LogcatDetectorState(permission_ok=True, started=True),
            ),
        ):
            detector.poll_logcat()

        row_a = detector._states["com.moons.litesa"]
        row_b = detector._states["com.moons.litesb"]
        self.assertFalse(row_a.dead_confirmed)
        self.assertGreater(row_a.last_gamejoinloadtime_at, 0)
        self.assertEqual(row_b.last_gamejoinloadtime_at, 0.0)

    def test_logcat_with_reason_targets_uid_package_only(self) -> None:
        packages = ["com.moons.litesa", "com.moons.litesb"]
        with patch(
            "agent.android_logcat_detector.android.run_command",
            return_value=type(
                "R",
                (),
                {
                    "ok": True,
                    "stdout": "uid=10102 with reason disconnect",
                },
            )(),
        ):
            events, state = poll_logcat_events(
                packages,
                uid_map={"com.moons.litesa": "10101", "com.moons.litesb": "10102"},
            )
        self.assertTrue(state.permission_ok)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].package, "com.moons.litesb")
        self.assertEqual(events[0].event, "package_logcat_reason")

    def test_duplicate_dead_webhook_blocked_by_lifecycle_state(self) -> None:
        state_path = webhook.DATA_DIR / "package-lifecycle-webhook-state.json"
        state_path.unlink(missing_ok=True)
        webhook.mark_package_lifecycle_dead_notified("com.moons.litesa", username="UserA")
        self.assertTrue(webhook.package_lifecycle_dead_already_notified("com.moons.litesa"))

    def test_recovered_webhook_pending_after_dead_notified(self) -> None:
        state_path = webhook.DATA_DIR / "package-lifecycle-webhook-state.json"
        state_path.unlink(missing_ok=True)
        webhook.mark_package_lifecycle_dead_notified("com.moons.litesa", username="UserA")
        self.assertTrue(webhook.package_lifecycle_recover_pending("com.moons.litesa"))
        webhook.mark_package_lifecycle_recovered("com.moons.litesa", username="UserA")
        self.assertFalse(webhook.package_lifecycle_recover_pending("com.moons.litesa"))

    def test_process_missing_confirm_checks_constant(self) -> None:
        self.assertEqual(PROCESS_MISSING_CONFIRM_CHECKS, 2)


if __name__ == "__main__":
    unittest.main()
