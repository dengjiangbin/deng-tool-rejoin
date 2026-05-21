"""Regression tests for probe p-447f94dd3b release blockers."""

from __future__ import annotations

import inspect
import signal
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


def _pkgs(n: int) -> list[str]:
    return [f"pkg{i}" for i in range(1, n + 1)]


class TestSplitLayoutP447(unittest.TestCase):
    def _rects(self, n: int, *, mode: str = "landscape", w: int = 1280, h: int = 720):
        from agent import window_layout as wl
        with mock.patch("agent.window_layout._detect_status_bar_height", return_value=25):
            return wl.calculate_split_layout(_pkgs(n), w, h, termux_log_fraction=0.50, screen_mode=mode)

    def test_landscape_lte6_uses_empty_left_column(self) -> None:
        rects = self._rects(6)
        self.assertEqual([(r.left, r.top) for r in rects], [
            (853, 25), (1066, 25),
            (853, 256), (1066, 256),
            (853, 487), (1066, 487),
        ])

    def test_landscape_7_to_9_uses_full_3x3(self) -> None:
        rects = self._rects(9)
        self.assertEqual([(r.left, r.top) for r in rects[:9]], [
            (640, 25), (853, 25), (1066, 25),
            (640, 256), (853, 256), (1066, 256),
            (640, 487), (853, 487), (1066, 487),
        ])

    def test_roblox_grid_never_uses_left_termux_half(self) -> None:
        for mode, w, h, n in (("landscape", 1280, 720, 6), ("portrait", 720, 1280, 10)):
            rects = self._rects(n, mode=mode, w=w, h=h)
            left_end = round(w * 0.50)
            for rect in rects:
                self.assertGreaterEqual(rect.left, left_end)
                self.assertGreater(rect.right, rect.left)
                self.assertLessEqual(rect.right, w)


class TestOrientationOverrideP447(unittest.TestCase):
    def test_orientation_override_detection_respects_protected_packages(self) -> None:
        import agent.android as android
        with mock.patch.object(android, "get_application_label", return_value=""):
            found = android.detect_orientation_override_apps(
                packages=[
                    "ahapps.controlthescreenorientation",
                    "com.termux",
                    "com.moons.litesc",
                ],
                protected_packages=["com.moons.litesc"],
            )
        self.assertEqual([row["package"] for row in found], ["ahapps.controlthescreenorientation"])

    def test_enforce_force_stops_only_orientation_override_when_it_wins(self) -> None:
        import agent.android as android
        states = [
            {"orientation": "landscape"},
            {"orientation": "landscape"},
            {"orientation": "portrait"},
        ]
        with mock.patch.object(android, "detect_root", return_value=android.RootInfo(True, "su", "uid=0")), \
             mock.patch.object(android, "get_display_orientation_state", side_effect=states), \
             mock.patch.object(android, "_apply_user_rotation", return_value=[]), \
             mock.patch.object(android, "detect_orientation_override_apps", return_value=[
                 {"package": "ahapps.controlthescreenorientation", "label": "Control Screen Orientation", "reason": "package_name"}
             ]), \
             mock.patch.object(android, "force_stop_package", return_value=android.CommandResult(("am",), 0, "", "")) as stop:
            result = android.enforce_screen_orientation("portrait", protected_packages=["com.termux", "com.moons.litesc"])
        self.assertTrue(result["success"])
        stop.assert_called_once()
        self.assertEqual(stop.call_args.args[0], "ahapps.controlthescreenorientation")


class TestLifecycleP447(unittest.TestCase):
    def test_signal_stop_source_is_recorded(self) -> None:
        from agent.supervisor import WatchdogSupervisor
        sup = WatchdogSupervisor([{"package": "com.roblox.client", "enabled": True}], {"supervisor": {}})
        sup._handle_stop(signal.SIGINT, None)
        self.assertTrue(sup.stop_event.is_set())
        self.assertEqual(sup.stop_source, "sigint")

    def test_programmatic_stop_source_is_recorded(self) -> None:
        from agent.supervisor import WatchdogSupervisor
        sup = WatchdogSupervisor([{"package": "com.roblox.client", "enabled": True}], {"supervisor": {}})
        sup.stop("fatal_error")
        self.assertTrue(sup.stop_event.is_set())
        self.assertEqual(sup.stop_source, "fatal_error")

    def test_cmd_start_contains_monitoring_guard(self) -> None:
        import agent.commands as commands
        src = inspect.getsource(commands.cmd_start)
        self.assertIn("[DENG_REJOIN_MONITOR_ENTER]", src)
        self.assertIn("[DENG_REJOIN_UNEXPECTED_EXIT_GUARD]", src)
        self.assertIn('"MONITORING"', src)


class TestRamAboveTableP447(unittest.TestCase):
    def test_cmd_start_renders_ram_before_table_in_phase_and_live_dashboard(self) -> None:
        import agent.commands as commands
        src = inspect.getsource(commands.cmd_start)
        phase_body = src.split("def _render_phase", 1)[1].split("def _set_all_phase", 1)[0]
        live_body = src.split("def _live_dashboard", 1)[1].split("# Use 3-second", 1)[0]
        self.assertLess(phase_body.find("ram = _get_ram_label()"), phase_body.find("build_start_table(rows"))
        self.assertLess(live_body.find("ram_label = _get_ram_label()"), live_body.find("build_start_table(live_rows"))


if __name__ == "__main__":
    unittest.main()
