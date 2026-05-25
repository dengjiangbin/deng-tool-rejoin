from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from agent import android, window_apply, window_layout
from agent.window_layout import WindowRect


def _pkgs(count: int) -> list[str]:
    return [f"com.moons.lite{i}" for i in range(1, count + 1)]


class TestProbeA434c432daPortraitTouchLayout(unittest.TestCase):
    def test_portrait_orientation_normalizes_landscape_raw_size(self) -> None:
        self.assertEqual(
            window_layout.normalize_display_for_screen_mode(1280, 720, "portrait"),
            (720, 1280),
        )
        rects = window_layout.calculate_split_layout(
            _pkgs(2), 1280, 720, termux_log_fraction=0.50, screen_mode="portrait",
        )
        self.assertGreater(rects[0].bottom, rects[0].right)
        self.assertLessEqual(max(r.right for r in rects), 720)
        self.assertLessEqual(max(r.bottom for r in rects), 1280)

    def test_portrait_slots_are_touch_safe_for_required_counts(self) -> None:
        for count in (1, 2, 3, 4, 6, 9, 10):
            with self.subTest(count=count):
                rects = window_layout.calculate_split_layout(
                    _pkgs(count), 720, 1280,
                    termux_log_fraction=0.50,
                    screen_mode="portrait",
                )
                self.assertEqual(
                    window_layout.validate_portrait_touch_layout(rects, 720, 1280),
                    [],
                )

    def test_portrait_touch_validation_catches_offscreen_overlap_termux_and_small(self) -> None:
        rects = [
            WindowRect("p1", 0, 0, 100, 100),
            WindowRect("p2", 50, 50, 300, 220),
            WindowRect("p3", 600, 1100, 760, 1320),
        ]
        errors = window_layout.validate_portrait_touch_layout(
            rects, 720, 1280, termux_bounds=(0, 0, 200, 220),
        )
        joined = "\n".join(errors)
        self.assertIn("touch width too small", joined)
        self.assertIn("touch height too small", joined)
        self.assertIn("offscreen", joined)
        self.assertIn("overlaps termux", joined)
        self.assertIn("overlaps rect", joined)

    def test_portrait_xml_writer_uses_portrait_not_landscape_flags(self) -> None:
        root = ET.Element("map")
        rect = WindowRect("pkg", 0, 512, 360, 768)

        changed = window_layout._apply_layout_keys_to_root(
            root, rect, screen_mode="portrait",
        )

        values = {child.attrib["name"]: child.attrib.get("value") for child in root}
        self.assertGreater(changed, 0)
        self.assertEqual(values["app_cloner_window_position_portrait_left"], "0")
        self.assertEqual(values["app_cloner_window_size_portrait_height"], "256")
        self.assertEqual(values["app_cloner_force_portrait"], "true")
        self.assertEqual(values["app_cloner_force_landscape"], "false")

    def test_landscape_xml_writer_preserves_landscape_flags(self) -> None:
        root = ET.Element("map")
        rect = WindowRect("pkg", 426, 25, 852, 256)

        window_layout._apply_layout_keys_to_root(
            root, rect, screen_mode="landscape",
        )

        values = {child.attrib["name"]: child.attrib.get("value") for child in root}
        self.assertEqual(values["app_cloner_force_landscape"], "true")
        self.assertEqual(values["app_cloner_force_portrait"], "false")

    def test_probe_failure_identical_actual_bounds_detected_as_overlap(self) -> None:
        desired = [
            WindowRect("com.moons.litesc", 0, 512, 360, 768),
            WindowRect("com.moons.litesd", 360, 512, 720, 768),
            WindowRect("com.moons.litese", 0, 768, 360, 1024),
        ]
        actual = {
            "com.moons.litesc": (0, 438, 720, 843),
            "com.moons.litesd": (0, 438, 720, 843),
            "com.moons.litese": (0, 438, 720, 843),
        }

        def fake_read_bounds(pkg: str):
            return actual[pkg], "dumpsys_window"

        with mock.patch.object(window_apply, "_capability_probes", return_value={}), \
             mock.patch.object(android, "detect_root", return_value=android.RootInfo(False, None, "")), \
             mock.patch.object(window_apply, "_discover_known_keys", return_value={}), \
             mock.patch.object(window_apply, "_write_one_package", side_effect=lambda rect, result, **_: setattr(result, "pre_write_ok", True) or True), \
             mock.patch.object(window_apply, "_wait_for_window", return_value=True), \
             mock.patch.object(window_apply, "read_actual_bounds", side_effect=fake_read_bounds), \
             mock.patch.object(window_apply, "_display_bounds", return_value=(0, 0, 720, 1280)):
            results = window_apply.apply_window_layout(
                desired, verify_after=True, retries=0, screen_mode="portrait",
            )

        self.assertTrue(all(not r.final_ok for r in results))
        self.assertTrue(all(r.status == window_apply.LAYOUT_FAILED for r in results))
        self.assertTrue(all(any(v.startswith("Overlap:") for v in r.validation) for r in results))

    def test_rc_zero_task_match_but_input_mismatch_is_failure(self) -> None:
        rect = WindowRect("com.moons.litesc", 0, 512, 360, 768)
        layer = {
            "task_bounds": [0, 512, 360, 768],
            "surface_bounds": [0, 512, 360, 768],
            "input_region": [0, 536, 360, 792],
            "touchable_region": [0, 536, 360, 792],
            "window_frame": [0, 512, 360, 768],
            "content_frame": [0, 536, 360, 768],
            "title_bar_height": 24,
            "corrected_task_bounds": [0, 536, 360, 792],
            "density": {"wm_physical_density": 420},
            "mismatch_classification": [
                "visual_correct_input_wrong",
                "decor_title_bar_offset",
            ],
        }

        with mock.patch.object(window_apply, "_capability_probes", return_value={}), \
             mock.patch.object(android, "detect_root", return_value=android.RootInfo(False, None, "")), \
             mock.patch.object(window_apply, "_discover_known_keys", return_value={}), \
             mock.patch.object(window_apply, "_write_one_package", side_effect=lambda rect, result, **_: setattr(result, "pre_write_ok", True) or True), \
             mock.patch.object(window_apply, "_wait_for_window", return_value=True), \
             mock.patch.object(window_apply, "read_actual_bounds", return_value=((0, 512, 360, 768), "dumpsys_window")), \
             mock.patch.object(window_apply, "collect_portrait_layer_readback", return_value=layer), \
             mock.patch.object(window_apply, "_display_bounds", return_value=(0, 0, 720, 1280)):
            results = window_apply.apply_window_layout(
                [rect], verify_after=True, retries=0, screen_mode="portrait",
            )

        self.assertFalse(results[0].final_ok)
        self.assertEqual(results[0].status, window_apply.LAYOUT_FAILED)
        self.assertIn("visual correct input wrong", results[0].validation)
        self.assertEqual(results[0].input_region, (0, 536, 360, 792))

    def test_portrait_touch_probe_taps_center_inside_actual_window(self) -> None:
        rect = WindowRect("com.moons.litesc", 0, 512, 360, 768)
        taps: list[list[str]] = []

        def fake_root_command(cmd, root_tool=None, timeout=None):
            taps.append(list(cmd))
            class _R:
                ok = True
                stdout = ""
                stderr = ""
            return _R()

        with mock.patch.object(window_apply, "_capability_probes", return_value={}), \
             mock.patch.object(android, "detect_root", return_value=android.RootInfo(True, "su", "")), \
             mock.patch.object(window_apply, "_discover_known_keys", return_value={}), \
             mock.patch.object(window_apply, "_write_one_package", side_effect=lambda rect, result, **_: setattr(result, "pre_write_ok", True) or True), \
             mock.patch.object(window_apply, "_wait_for_window", return_value=True), \
             mock.patch.object(window_apply, "read_actual_bounds", return_value=((0, 512, 360, 768), "dumpsys_window")), \
             mock.patch.object(window_apply, "_display_bounds", return_value=(0, 0, 720, 1280)), \
             mock.patch.object(android, "run_root_command", side_effect=fake_root_command):
            results = window_apply.apply_window_layout(
                [rect], verify_after=True, retries=0,
                screen_mode="portrait", touch_probe=True,
            )

        self.assertTrue(results[0].final_ok)
        self.assertEqual(results[0].touch_probe_center, (180, 640))
        self.assertEqual(taps[-1], ["input", "tap", "180", "640"])

    def test_touch_probe_failure_marks_portrait_layout_failed(self) -> None:
        rect = WindowRect("com.moons.litesc", 0, 512, 360, 768)

        def fake_root_command(cmd, root_tool=None, timeout=None):
            class _R:
                ok = False
                stdout = ""
                stderr = "tap rejected"
            return _R()

        layer = {
            "task_bounds": [0, 512, 360, 768],
            "surface_bounds": [0, 512, 360, 768],
            "input_region": [0, 512, 360, 768],
            "mismatch_classification": ["match"],
        }
        with mock.patch.object(window_apply, "_capability_probes", return_value={}), \
             mock.patch.object(android, "detect_root", return_value=android.RootInfo(True, "su", "")), \
             mock.patch.object(window_apply, "_discover_known_keys", return_value={}), \
             mock.patch.object(window_apply, "_write_one_package", side_effect=lambda rect, result, **_: setattr(result, "pre_write_ok", True) or True), \
             mock.patch.object(window_apply, "_wait_for_window", return_value=True), \
             mock.patch.object(window_apply, "read_actual_bounds", return_value=((0, 512, 360, 768), "dumpsys_window")), \
             mock.patch.object(window_apply, "collect_portrait_layer_readback", return_value=layer), \
             mock.patch.object(window_apply, "_display_bounds", return_value=(0, 0, 720, 1280)), \
             mock.patch.object(android, "run_root_command", side_effect=fake_root_command):
            results = window_apply.apply_window_layout(
                [rect], verify_after=True, retries=0,
                screen_mode="portrait", touch_probe=True,
            )

        self.assertFalse(results[0].final_ok)
        self.assertEqual(results[0].status, window_apply.LAYOUT_FAILED)
        self.assertIn("touch probe failed", results[0].validation)

    def test_landscape_slot_order_remains_unchanged(self) -> None:
        expected = [
            (853, 25, 1066, 195),
            (1066, 25, 1280, 195),
            (853, 256, 1066, 426),
            (1066, 256, 1280, 426),
            (853, 487, 1066, 657),
            (1066, 487, 1280, 657),
        ]
        with mock.patch("agent.window_layout._detect_status_bar_height", return_value=25):
            rects = window_layout.calculate_split_layout(
                _pkgs(6), 1280, 720,
                termux_log_fraction=0.50,
                screen_mode="landscape",
            )
        self.assertEqual(
            [(r.left, r.top, r.right, r.bottom) for r in rects],
            expected,
        )


if __name__ == "__main__":
    unittest.main()
