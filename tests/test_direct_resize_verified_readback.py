"""Regression: direct root resize must not report success on false rc=0."""

from __future__ import annotations

import unittest
from unittest import mock

from agent import window_apply
from agent.window_layout import WindowRect


class TestDirectResizeVerifiedReadback(unittest.TestCase):
    def test_apply_marks_direct_resize_false_when_readback_stays_fullscreen(self) -> None:
        rect = WindowRect("com.moons.litesc", 0, 25, 720, 652)

        def fake_direct(package, desired, root_tool, **kwargs):
            return False, "am stack resize rc=0 but bounds=(0, 0, 720, 1251)"

        with mock.patch.object(window_apply.android, "detect_root",
                              return_value=window_apply.android.RootInfo(True, "su", "")), \
             mock.patch.object(window_apply, "_capability_probes",
                              return_value={"root": True}), \
             mock.patch.object(window_apply, "_discover_known_keys", return_value={}), \
             mock.patch.object(window_apply, "_write_one_package", return_value=True), \
             mock.patch.object(window_apply, "_wait_for_window", return_value=True), \
             mock.patch.object(window_apply, "read_actual_bounds",
                              return_value=((0, 0, 720, 1251), "dumpsys_window")), \
             mock.patch.object(window_apply, "_direct_resize_via_root", side_effect=fake_direct), \
             mock.patch("agent.freeform_enable.setup_freeform_capabilities", return_value=mock.Mock()):
            results = window_apply.apply_window_layout(
                [rect],
                verify_after=True,
                retries=0,
                screen_mode="portrait",
            )

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].direct_resize_ok)
        self.assertFalse(results[0].final_ok)
        self.assertEqual(results[0].status, window_apply.LAYOUT_FAILED)


if __name__ == "__main__":
    unittest.main()
