from __future__ import annotations

import unittest
from unittest import mock

from agent import resize_mode


class TestDetectEffectiveResizeMode(unittest.TestCase):
    def _run(self, **patches):
        defaults = {
            "wm": (1080, 1920, "Physical size: 1080x1920"),
            "display": {"orientation": "portrait", "width": 1080, "height": 1920, "rotation": 0},
            "logical": None,
            "home_landscape": (False, ""),
            "focus": (0, 0, ""),
        }
        defaults.update(patches)
        wm_w, wm_h, wm_raw = defaults["wm"]
        logical = defaults["logical"]
        if logical is None:
            logical_block = None
        else:
            logical_block = (logical[0], logical[1], 420)

        with mock.patch("agent.resize_mode.android.get_wm_size", return_value={
            "width": wm_w, "height": wm_h, "raw": wm_raw, "ok": True,
        }), mock.patch("agent.resize_mode._display_state", return_value=defaults["display"]), \
             mock.patch("agent.resize_mode._detect_display_from_dumpsys", return_value=logical_block), \
             mock.patch("agent.resize_mode._home_launcher_landscape", return_value=defaults["home_landscape"]), \
             mock.patch("agent.resize_mode._current_focus_size", return_value=defaults["focus"]):
            return resize_mode.detect_effective_resize_mode()

    def test_wm_portrait_rotation0_logical_portrait(self):
        r = self._run(
            display={"orientation": "portrait", "width": 1080, "height": 1920, "rotation": 0},
            logical=(1080, 1920),
        )
        self.assertEqual(r["mode"], "PORTRAIT")
        self.assertIn(r["confidence"], {"HIGH", "MEDIUM"})

    def test_wm_portrait_rotation1_logical_landscape(self):
        r = self._run(
            display={"orientation": "landscape", "width": 1920, "height": 1080, "rotation": 1},
            logical=(1920, 1080),
        )
        self.assertEqual(r["mode"], "LANDSCAPE")
        self.assertEqual(r["confidence"], "HIGH")

    def test_home_landscape_wm_portrait_conflict(self):
        r = self._run(
            display={"orientation": "landscape", "width": 1920, "height": 1080, "rotation": 1},
            logical=(1920, 1080),
            home_landscape=(True, "com.android.launcher3"),
        )
        self.assertTrue(r["signals"]["home_landscape_wm_portrait_conflict"])
        self.assertEqual(r["mode"], "LANDSCAPE")
        self.assertIn("portrait", r["basis"])

    def test_focus_landscape_unknown_rotation(self):
        r = self._run(
            display={"orientation": "unknown", "width": 0, "height": 0, "rotation": ""},
            logical=(1920, 1080),
            focus=(1920, 1080, "com.android.launcher3"),
        )
        self.assertEqual(r["mode"], "LANDSCAPE")

    def test_missing_logical_fallback_portrait(self):
        r = self._run(
            display={"orientation": "portrait", "width": 1080, "height": 1920, "rotation": 0},
            logical=None,
            focus=(0, 0, ""),
        )
        self.assertEqual(r["mode"], "PORTRAIT")
        self.assertIn(r["confidence"], {"HIGH", "MEDIUM", "LOW"})

    def test_conflicting_signals_recorded(self):
        r = self._run(
            display={"orientation": "portrait", "width": 1080, "height": 1920, "rotation": 0},
            logical=(1920, 1080),
            home_landscape=(False, ""),
        )
        self.assertTrue(r["conflicts"] or r["mode"] in {"LANDSCAPE", "PORTRAIT"})

    def test_previous_mode_fallback_on_tie(self):
        with mock.patch("agent.resize_mode.android.get_wm_size", return_value={
            "width": 0, "height": 0, "raw": "", "ok": False,
        }), mock.patch("agent.resize_mode._display_state", return_value={
            "orientation": "unknown", "width": 0, "height": 0, "rotation": "",
        }), mock.patch("agent.resize_mode._detect_display_from_dumpsys", return_value=None), \
             mock.patch("agent.resize_mode._home_launcher_landscape", return_value=(False, "")), \
             mock.patch("agent.resize_mode._current_focus_size", return_value=(0, 0, "")):
            r2 = resize_mode.detect_effective_resize_mode(previous_mode="LANDSCAPE")
        self.assertEqual(r2["mode"], "LANDSCAPE")
        self.assertEqual(r2["confidence"], "LOW")


if __name__ == "__main__":
    unittest.main()
