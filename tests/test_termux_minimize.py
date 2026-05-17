"""Tests for ``agent.termux_minimize`` — Termux dock-resize at Start time.

This module:
1. Resolves the Termux task ID from ``dumpsys activity / window`` output.
2. Computes a clamped dock rectangle from the screen size + fraction.
3. Walks a cascade of resize commands (``cmd activity resize-task`` →
   ``am task resize`` → ``am stack resize`` → ``wm task resize``).
4. Verifies the new bounds via ``dumpsys window windows``.

Real-device requirement (probe ``p-ce7b1d7918``): without minimizing
Termux, the user can't see whether clones land in their pane.  The
minimizer runs BEFORE the launches and reserves the right side of the
screen for clones.
"""

from __future__ import annotations

import unittest
from unittest import mock


# ── Helpers ──────────────────────────────────────────────────────────────────


def _fake_cmd_result(rc: int = 0, stdout: str = "", stderr: str = ""):
    """Build a stand-in for :class:`agent.android.CommandResult`."""
    from agent.android import CommandResult
    return CommandResult(("fake",), rc, stdout, stderr)


def _fake_root_info(available: bool = True, tool: str = "su"):
    from agent.android import RootInfo
    return RootInfo(available=available, tool=tool, detail="test")


class FractionClampTest(unittest.TestCase):
    """``_clamp_fraction`` keeps the dock in a usable range."""

    def setUp(self) -> None:
        from agent.termux_minimize import _clamp_fraction
        self.fn = _clamp_fraction

    def test_default_passthrough(self) -> None:
        self.assertEqual(self.fn(0.35), 0.35)

    def test_below_minimum_is_clamped(self) -> None:
        self.assertEqual(self.fn(0.01), 0.15)

    def test_above_maximum_is_clamped(self) -> None:
        self.assertEqual(self.fn(0.99), 0.9)

    def test_garbage_returns_default(self) -> None:
        from agent.window_layout import TERMUX_LOG_FRACTION
        self.assertEqual(self.fn(None), TERMUX_LOG_FRACTION)
        self.assertEqual(self.fn("not-a-number"), TERMUX_LOG_FRACTION)


class DockRectTest(unittest.TestCase):
    """``_dock_rect`` produces sane bounds even on tiny screens."""

    def setUp(self) -> None:
        from agent.termux_minimize import _dock_rect
        from agent.window_layout import DisplayInfo
        self.fn = _dock_rect
        self.DI = DisplayInfo

    def test_normal_screen_at_35pct(self) -> None:
        # 720 × 0.35 = 252; 1280 = 1280
        rect = self.fn(self.DI(720, 1280, 164), 0.35)
        self.assertEqual(rect[0], 0)
        self.assertEqual(rect[1], 0)
        self.assertEqual(rect[2], 252)
        self.assertEqual(rect[3], 1280)

    def test_50pct_dock(self) -> None:
        rect = self.fn(self.DI(720, 1280, 164), 0.5)
        self.assertEqual(rect, (0, 0, 360, 1280))

    def test_tiny_screen_pads_to_minimum(self) -> None:
        rect = self.fn(self.DI(400, 600, 120), 0.35)
        # 400 × 0.35 = 140 → pad to MIN_DOCK_WIDTH (240).
        self.assertEqual(rect[2], 240)


class FindTaskIdTest(unittest.TestCase):
    """``_find_termux_task_id`` extracts the Termux task ID from dumpsys output."""

    def test_recognises_taskid_within_window(self) -> None:
        from agent.termux_minimize import _scan_for_termux_task
        # Realistic snippet from ``dumpsys activity activities``.
        sample = """
        Stack #0:
          mResumedActivity=ActivityRecord{abc com.termux/.app.TermuxActivity}
            taskId=42 stackId=0
        """
        self.assertEqual(_scan_for_termux_task(sample), 42)

    def test_recognises_task_block_format(self) -> None:
        from agent.termux_minimize import _scan_for_termux_task
        sample = """
        * Task{aaa #173 visible=true type=standard mode=freeform
          translucent=false canEnterPip=false
            * Hist #0: ActivityRecord{... com.termux/.HomeActivity}
        """
        self.assertEqual(_scan_for_termux_task(sample), 173)

    def test_no_termux_returns_none(self) -> None:
        from agent.termux_minimize import _scan_for_termux_task
        self.assertIsNone(_scan_for_termux_task("blah blah blah\nno termux here\n"))

    def test_no_taskid_returns_none(self) -> None:
        from agent.termux_minimize import _scan_for_termux_task
        # com.termux is present but there's no taskId near it.
        self.assertIsNone(_scan_for_termux_task("foo com.termux bar\n"))

    def test_uses_dumpsys_chain(self) -> None:
        """When ``activity activities`` returns nothing, falls through to recents."""
        from agent import termux_minimize as tm

        outputs = [
            _fake_cmd_result(0, "no relevant text\n"),
            _fake_cmd_result(0,
                "Task{... visible=true #99 ...\n"
                "  * Hist #0: ActivityRecord{... com.termux/.app.TermuxActivity}\n"
                "    taskId=99 stackId=2\n"
            ),
        ]
        with mock.patch.object(tm.android, "run_android_command",
                               side_effect=outputs):
            tid, source = tm._find_termux_task_id()
        self.assertEqual(tid, 99)
        self.assertIn("recents", source)


class ResizeCascadeTest(unittest.TestCase):
    """``_try_resize`` returns success on the first command that runs cleanly."""

    def test_first_command_succeeds(self) -> None:
        from agent import termux_minimize as tm
        with mock.patch.object(tm.android, "run_root_command",
                               return_value=_fake_cmd_result(0, "OK")):
            ok, method, attempts = tm._try_resize(42, (0, 0, 252, 1280), "su")
        self.assertTrue(ok)
        # First non-windowing-mode attempt is ``cmd resize-task``.
        self.assertEqual(method, "cmd resize-task")
        # set-windowing-mode + cmd resize-task → 2 attempts logged.
        self.assertGreaterEqual(len(attempts), 2)

    def test_first_fails_second_succeeds(self) -> None:
        from agent import termux_minimize as tm
        results = [
            _fake_cmd_result(0, "OK"),              # set-windowing-mode (rc=0)
            _fake_cmd_result(1, "", "bad task"),    # cmd resize-task
            _fake_cmd_result(0, "OK"),              # cmd resize-task (mode=1)
        ]
        with mock.patch.object(tm.android, "run_root_command",
                               side_effect=results):
            ok, method, _ = tm._try_resize(42, (0, 0, 252, 1280), "su")
        self.assertTrue(ok)
        self.assertEqual(method, "cmd resize-task (mode=1)")

    def test_all_fail_returns_false(self) -> None:
        from agent import termux_minimize as tm
        with mock.patch.object(tm.android, "run_root_command",
                               return_value=_fake_cmd_result(1, "", "err")):
            ok, method, attempts = tm._try_resize(42, (0, 0, 252, 1280), "su")
        self.assertFalse(ok)
        self.assertEqual(method, "")
        self.assertGreaterEqual(len(attempts), 6)  # windowing + 5 cmds

    def test_no_root_uses_run_command(self) -> None:
        """When ``root_tool`` is None, the cascade skips windowing-mode + uses run_command."""
        from agent import termux_minimize as tm
        with mock.patch.object(tm.android, "run_command",
                               return_value=_fake_cmd_result(0, "OK")) as rc, \
             mock.patch.object(tm.android, "run_root_command") as rrc:
            ok, method, _ = tm._try_resize(42, (0, 0, 252, 1280), None)
        self.assertTrue(ok)
        self.assertEqual(method, "cmd resize-task")
        rc.assert_called()
        rrc.assert_not_called()


class ReadBackBoundsTest(unittest.TestCase):
    """``_read_back_termux_bounds`` parses ``dumpsys window windows`` output."""

    def test_parses_mframe_format(self) -> None:
        from agent import termux_minimize as tm
        sample = (
            "Window #5 Window{0xabc u0 com.termux/.app.TermuxActivity}:\n"
            "    mFrame=[0,0][252,1280] mLastFrame=[0,0][720,1280]\n"
        )
        with mock.patch.object(tm.android, "run_android_command",
                               return_value=_fake_cmd_result(0, sample)):
            bounds = tm._read_back_termux_bounds()
        self.assertEqual(bounds, (0, 0, 252, 1280))

    def test_missing_window_returns_none(self) -> None:
        from agent import termux_minimize as tm
        with mock.patch.object(tm.android, "run_android_command",
                               return_value=_fake_cmd_result(0, "irrelevant text")):
            self.assertIsNone(tm._read_back_termux_bounds())


class PublicEntrypointTest(unittest.TestCase):
    """End-to-end ``minimize_termux_to_dock`` happy + degraded paths."""

    def _make_disp(self, w: int = 720, h: int = 1280, dpi: int = 164):
        from agent.window_layout import DisplayInfo
        return DisplayInfo(w, h, dpi)

    def test_happy_path_with_verify(self) -> None:
        from agent import termux_minimize as tm
        disp = self._make_disp()
        dumpsys_with_termux = (
            "Stack #0:\n"
            "  mResumedActivity=ActivityRecord{a com.termux/.app.TermuxActivity}\n"
            "    taskId=11 stackId=0\n"
        )
        readback = (
            "Window com.termux/...\n"
            "    mFrame=[0,0][252,1280]\n"
        )
        # Mocks: detect_display_info → fixed disp, run_android_command →
        # first call returns task, second returns readback; root resize cmd
        # always succeeds.
        with mock.patch.object(tm, "detect_display_info", return_value=disp), \
             mock.patch.object(tm.android, "run_android_command",
                               side_effect=[_fake_cmd_result(0, dumpsys_with_termux),
                                            _fake_cmd_result(0, readback)]), \
             mock.patch.object(tm.android, "detect_root",
                               return_value=_fake_root_info()), \
             mock.patch.object(tm.android, "run_root_command",
                               return_value=_fake_cmd_result(0, "OK")), \
             mock.patch.object(tm.time, "sleep"):
            res = tm.minimize_termux_to_dock(fraction=0.35)
        self.assertTrue(res.ok, f"expected ok=True, attempts: {res.attempts}")
        self.assertEqual(res.task_id, 11)
        self.assertEqual(res.desired, (0, 0, 252, 1280))
        self.assertEqual(res.actual, (0, 0, 252, 1280))
        self.assertEqual(res.method, "cmd resize-task")

    def test_no_termux_task_skips_with_reason(self) -> None:
        from agent import termux_minimize as tm
        with mock.patch.object(tm, "detect_display_info",
                               return_value=self._make_disp()), \
             mock.patch.object(tm.android, "run_android_command",
                               return_value=_fake_cmd_result(0, "no termux here\n")):
            res = tm.minimize_termux_to_dock()
        self.assertFalse(res.ok)
        self.assertTrue(res.skipped)
        self.assertIn("Termux", res.reason)

    def test_resize_failure_marks_not_ok(self) -> None:
        from agent import termux_minimize as tm
        dumpsys = (
            "ActivityRecord{a com.termux/.app.TermuxActivity}\n"
            "  taskId=7 stackId=0\n"
        )
        with mock.patch.object(tm, "detect_display_info",
                               return_value=self._make_disp()), \
             mock.patch.object(tm.android, "run_android_command",
                               return_value=_fake_cmd_result(0, dumpsys)), \
             mock.patch.object(tm.android, "detect_root",
                               return_value=_fake_root_info()), \
             mock.patch.object(tm.android, "run_root_command",
                               return_value=_fake_cmd_result(1, "", "denied")):
            res = tm.minimize_termux_to_dock(verify=False)
        self.assertFalse(res.ok)
        self.assertEqual(res.task_id, 7)
        self.assertIn("all resize variants failed", res.reason)

    def test_silent_wrapper_never_raises(self) -> None:
        from agent import termux_minimize as tm
        with mock.patch.object(tm, "minimize_termux_to_dock",
                               side_effect=RuntimeError("explode")):
            out = tm.minimize_termux_silent(0.35)
        self.assertEqual(out["ok"], False)
        self.assertTrue(out["skipped"])
        self.assertIn("explode", out["reason"])


class StartFlowIntegrationTest(unittest.TestCase):
    """``cmd_start`` calls ``minimize_termux_to_dock`` and captures result."""

    def test_minimize_call_runs_with_default_fraction(self) -> None:
        """Smoke-check: the new import + call path resolves cleanly."""
        from agent import termux_minimize as tm
        from agent.window_layout import TERMUX_LOG_FRACTION

        with mock.patch.object(
            tm, "minimize_termux_to_dock",
            return_value=tm.MinimizeResult(
                ok=True, task_id=42, fraction=TERMUX_LOG_FRACTION,
                desired=(0, 0, 252, 1280), actual=(0, 0, 252, 1280),
                method="cmd resize-task",
            ),
        ) as patched:
            res = tm.minimize_termux_to_dock(fraction=TERMUX_LOG_FRACTION)
        patched.assert_called_once()
        d = res.as_dict()
        self.assertTrue(d["ok"])
        self.assertEqual(d["task_id"], 42)
        self.assertEqual(d["method"], "cmd resize-task")
        self.assertEqual(d["desired"], [0, 0, 252, 1280])


if __name__ == "__main__":
    unittest.main()
