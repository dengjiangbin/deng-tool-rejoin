"""Tests for the multi-layer window-force pipeline.

Covers:
- ``freeform_enable.setup_freeform_capabilities`` probes + writes the right
  global / secure settings keys.
- ``android.launch_package_with_bounds`` builds the correct ``am start``
  arguments (``--windowingMode 5 --activity-launch-bounds l t r b``).
- ``window_apply._direct_resize_via_root`` tries multiple variants and
  succeeds on whichever works.
- ``window_apply.force_resize_package`` performs the full single-package
  recovery resize and verifies bounds.
- Aliveness / process-detection helpers don't false-negative on long clone
  package names (the cloud-phone bug).
"""

from __future__ import annotations

import unittest
from unittest import mock

from agent import android, freeform_enable, window_apply
from agent.window_layout import WindowRect


# ─── freeform_enable ─────────────────────────────────────────────────────────

class TestFreeformEnable(unittest.TestCase):
    def _patch(self, get_return: dict[str, str | None], root_ok: bool = True):
        """Fixture: mock settings get/put and root detection."""
        captured: list[tuple[str, str, str]] = []

        def fake_get(ns, key):
            return get_return.get(f"{ns}:{key}")

        def fake_put(ns, key, value, root_tool="su"):
            captured.append((ns, key, value))
            return True

        def fake_detect():
            return android.RootInfo(root_ok, "su" if root_ok else None, "")

        return mock.patch.object(freeform_enable, "_settings_get", fake_get), \
               mock.patch.object(freeform_enable, "_settings_put_root", fake_put), \
               mock.patch.object(android, "detect_root", fake_detect), \
               captured

    def test_writes_all_disabled_global_keys_with_root(self) -> None:
        p1, p2, p3, captured = self._patch({"global:enable_freeform_support": "0",
                                            "global:force_resizable_activities": "0",
                                            "global:freeform_window_management": "0",
                                            "global:development_settings_enabled": "0"})
        with p1, p2, p3, mock.patch.object(
            freeform_enable, "_settings_get",
            side_effect=lambda ns, k: "1" if (ns, k, "1") in [
                (a, b, c) for (a, b, c) in captured
            ] else "0",
        ):
            res = freeform_enable.setup_freeform_capabilities()
        # All four global keys should have been WRITTEN (the second mock
        # answers "1" once we've written, so they end up "enabled").
        written_globals = [k for ns, k, _ in captured if ns == "global"]
        for key in (
            "enable_freeform_support", "force_resizable_activities",
            "freeform_window_management", "development_settings_enabled",
        ):
            self.assertIn(key, written_globals)
        self.assertEqual(res.root_available, True)

    def test_already_enabled_keys_are_not_rewritten(self) -> None:
        # All probed (global + secure) keys already 1 → no writes.
        p1, p2, p3, captured = self._patch({
            "global:enable_freeform_support": "1",
            "global:force_resizable_activities": "1",
            "global:freeform_window_management": "1",
            "global:development_settings_enabled": "1",
            "secure:enable_freeform_support": "1",
            "secure:force_resizable_activities": "1",
            "secure:freeform_window_management": "1",
        })
        with p1, p2, p3:
            res = freeform_enable.setup_freeform_capabilities()
        self.assertEqual(captured, [])
        self.assertGreaterEqual(len(res.already_enabled_keys), 4)

    def test_no_root_returns_failed_keys_without_writing(self) -> None:
        p1, p2, p3, captured = self._patch({}, root_ok=False)
        with p1, p2, p3:
            res = freeform_enable.setup_freeform_capabilities()
        self.assertEqual(captured, [])
        self.assertFalse(res.root_available)
        self.assertEqual(res.enabled_keys, [])

    def test_silent_wrapper_never_raises(self) -> None:
        with mock.patch.object(
            freeform_enable, "setup_freeform_capabilities",
            side_effect=RuntimeError("boom"),
        ):
            ok, total = freeform_enable.setup_freeform_capabilities_silent()
        self.assertEqual(ok, 0)
        self.assertGreater(total, 0)


# ─── launch with bounds ──────────────────────────────────────────────────────

class TestLaunchWithBounds(unittest.TestCase):
    def test_am_command_includes_windowing_mode_and_bounds(self) -> None:
        rect = (100, 200, 800, 600)
        captured: list[list[str]] = []

        def fake_run(cmd, timeout=None):
            captured.append(list(cmd))
            class _R:
                ok = True
                stdout = ""
                stderr = ""
            return _R()

        with mock.patch.object(android, "_find_command", lambda *a, **k: "am"), \
             mock.patch.object(android, "run_command", fake_run):
            res, label = android.launch_package_with_bounds(
                "com.example.clone1", rect, private_url=None,
            )
        self.assertTrue(res.ok)
        self.assertIn("am_bounds_mode5", label)
        # Find the call with --activity-launch-bounds and verify the four ints.
        found = False
        for call in captured:
            if "--activity-launch-bounds" in call:
                idx = call.index("--activity-launch-bounds")
                # The next 4 values should match the rect.
                self.assertEqual(call[idx + 1:idx + 5], ["100", "200", "800", "600"])
                self.assertIn("--windowingMode", call)
                wm_idx = call.index("--windowingMode")
                self.assertEqual(call[wm_idx + 1], "5")
                found = True
                break
        self.assertTrue(found, "expected an am start call with --activity-launch-bounds")

    def test_falls_back_to_no_bounds_when_first_call_fails(self) -> None:
        rect = (0, 0, 1000, 500)
        attempts = [0]

        def fake_run(cmd, timeout=None):
            attempts[0] += 1
            class _R:
                pass
            # First call (with bounds) fails; second call (without bounds) succeeds.
            _R.ok = attempts[0] >= 2
            _R.stdout = ""
            _R.stderr = ""
            return _R()

        with mock.patch.object(android, "_find_command", lambda *a, **k: "am"), \
             mock.patch.object(android, "run_command", fake_run):
            res, label = android.launch_package_with_bounds(
                "com.example.clone1", rect, private_url=None,
            )
        self.assertTrue(res.ok)
        self.assertGreaterEqual(attempts[0], 2)


# ─── direct resize via root ──────────────────────────────────────────────────

class TestDirectResize(unittest.TestCase):
    def test_first_variant_success_returns_true(self) -> None:
        rect = WindowRect(package="p", left=0, top=0, right=100, bottom=100)
        seen: list[list[str]] = []

        def fake_root(cmd, root_tool=None, timeout=None):
            seen.append(list(cmd))
            class _R:
                ok = True
                stdout = "Task 42 resized"
                stderr = ""
            return _R()

        with mock.patch.object(window_apply, "_get_task_id", return_value=42), \
             mock.patch.object(android, "run_root_command", fake_root):
            ok, detail = window_apply._direct_resize_via_root("p", rect, "su")
        self.assertTrue(ok)
        # We should have at least tried set-task-windowing-mode plus the resize.
        joined = [" ".join(c) for c in seen]
        self.assertTrue(any("resize-task" in s or "task resize" in s for s in joined))

    def test_all_variants_fail_returns_false(self) -> None:
        rect = WindowRect(package="p", left=0, top=0, right=100, bottom=100)

        def fake_root(cmd, root_tool=None, timeout=None):
            class _R:
                ok = False
                stdout = ""
                stderr = "not supported"
            return _R()

        with mock.patch.object(window_apply, "_get_task_id", return_value=42), \
             mock.patch.object(android, "run_root_command", fake_root):
            ok, detail = window_apply._direct_resize_via_root("p", rect, "su")
        self.assertFalse(ok)
        self.assertIn("all direct-resize variants failed", detail)

    def test_no_task_id_returns_false(self) -> None:
        rect = WindowRect(package="p", left=0, top=0, right=100, bottom=100)
        with mock.patch.object(window_apply, "_get_task_id", return_value=None):
            ok, detail = window_apply._direct_resize_via_root("p", rect, "su")
        self.assertFalse(ok)
        self.assertEqual(detail, "no task id")


# ─── force_resize_package end-to-end ─────────────────────────────────────────

class TestForceResizePackage(unittest.TestCase):
    def _rect(self) -> WindowRect:
        return WindowRect(package="p", left=10, top=20, right=510, bottom=320)

    def test_success_when_readback_matches(self) -> None:
        with mock.patch.object(window_apply, "_direct_resize_via_root",
                              return_value=(True, "ok")), \
             mock.patch.object(android, "detect_root",
                              return_value=android.RootInfo(True, "su", "")), \
             mock.patch.object(window_apply, "read_actual_bounds",
                              return_value=((10, 20, 510, 320), "dumpsys")):
            ok, detail = window_apply.force_resize_package("p", self._rect())
        self.assertTrue(ok)
        self.assertIn("verified", detail)

    def test_failure_when_readback_mismatches(self) -> None:
        with mock.patch.object(window_apply, "_direct_resize_via_root",
                              return_value=(True, "ok")), \
             mock.patch.object(android, "detect_root",
                              return_value=android.RootInfo(True, "su", "")), \
             mock.patch.object(window_apply, "read_actual_bounds",
                              return_value=((0, 0, 100, 100), "dumpsys")):
            ok, detail = window_apply.force_resize_package("p", self._rect())
        self.assertFalse(ok)

    def test_no_root_returns_false(self) -> None:
        with mock.patch.object(android, "detect_root",
                              return_value=android.RootInfo(False, None, "")):
            ok, detail = window_apply.force_resize_package("p", self._rect())
        self.assertFalse(ok)
        self.assertIn("no root", detail)

    def test_never_raises_on_resize_error(self) -> None:
        with mock.patch.object(window_apply, "_direct_resize_via_root",
                              side_effect=RuntimeError("boom")), \
             mock.patch.object(android, "detect_root",
                              return_value=android.RootInfo(True, "su", "")):
            ok, detail = window_apply.force_resize_package("p", self._rect())
        self.assertFalse(ok)


# ─── aliveness detection for long clone package names ───────────────────────

class TestAliveDetectionForClones(unittest.TestCase):
    """The cloud-phone case: long clone names defeat pidof but are alive."""

    def setUp(self) -> None:
        # Clear the shared dumpsys cache so stale results from a previous
        # test do not leak into the dumpsys-mocking tests below.
        from agent import dumpsys_cache
        dumpsys_cache.invalidate()

    def test_pgrep_finds_process_when_pidof_misses(self) -> None:
        # pidof returns nothing (truncation); pgrep -f matches full cmdline.
        calls: list[list[str]] = []

        def fake_run(cmd, timeout=None):
            calls.append(list(cmd))
            class _R:
                ok = False
                stdout = ""
                stderr = ""
            if cmd[:1] == ["pidof"]:
                return _R()
            if cmd[:2] == ["pgrep", "-f"]:
                _R.ok = True
                _R.stdout = "1234\n"
                return _R()
            return _R()

        with mock.patch.object(android, "run_command", fake_run):
            self.assertTrue(android.is_process_running("com.x.very.long.clone.name"))

    def test_window_visible_accepts_has_surface_variant(self) -> None:
        """``hasSurface=true`` (no m-prefix) should also count as visible."""
        dumpsys = (
            "Window{abc com.x.clone1/com.roblox.client.MainActivity}:\n"
            "  mDisplayId=0\n"
            "  hasSurface=true\n"
            "  mFrame=[0,0][1080,1920]\n"
        )

        def fake_run(cmd, timeout=None):
            class _R:
                ok = True
                stdout = dumpsys
                stderr = ""
            return _R()

        with mock.patch.object(android, "run_command", fake_run):
            self.assertTrue(android.is_package_window_visible("com.x.clone1"))

    def test_window_visible_accepts_focus_line(self) -> None:
        dumpsys = (
            "mCurrentFocus=Window{def com.x.clone2/com.roblox.client.MainActivity}\n"
            "Window{other com.other/.X}:\n"
            "  no surface\n"
        )

        def fake_run(cmd, timeout=None):
            class _R:
                ok = True
                stdout = dumpsys
                stderr = ""
            return _R()

        with mock.patch.object(android, "run_command", fake_run):
            self.assertTrue(android.is_package_window_visible("com.x.clone2"))

    def test_window_visible_rejects_block_with_no_drawing_marker(self) -> None:
        dumpsys = (
            "Window{xyz com.x.clone3/.X}:\n"
            "  mDestroying=true\n"
            "  no drawing markers here\n"
        )

        def fake_run(cmd, timeout=None):
            class _R:
                ok = True
                stdout = dumpsys
                stderr = ""
            return _R()

        with mock.patch.object(android, "run_command", fake_run):
            self.assertFalse(android.is_package_window_visible("com.x.clone3"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
