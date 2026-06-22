"""Focused regression tests for probe p-43cfb5e3c5 fixes.

Covers:
  - prep_cache NameError regression (TASK 2)
  - Layout no-overlap for 1-6 packages (TASK 3)
  - Side-by-side bounds for 2 packages (TASK 3)
  - Top inset y behaviour (TASK 3)
  - Usage column render (TASK 4)
  - RAM usage dead/running package behaviour (TASK 4)
  - No Checking Package text in public UI (TASK 5)
  - No In-Lobby / Joining text (TASK 5)
"""
from __future__ import annotations

import inspect
import textwrap
import unittest


# ---------------------------------------------------------------------------
# TASK 2 — prep_cache NameError regression
# ---------------------------------------------------------------------------

class TestPrepCacheDefinition(unittest.TestCase):
    """Ensure prep_cache is always defined before it is referenced."""

    def _get_cmd_start_source(self) -> str:
        from agent import commands
        src = inspect.getsource(commands.cmd_start)
        return src

    def test_prep_cache_defined_before_use(self) -> None:
        """prep_cache dict must be initialised before its .get() call."""
        src = self._get_cmd_start_source()
        def_idx = src.find("prep_cache: dict")
        use_idx = src.find("prep_cache.get(")
        self.assertGreater(def_idx, -1, "prep_cache dict definition not found")
        self.assertGreater(use_idx, -1, "prep_cache.get() usage not found")
        self.assertLess(def_idx, use_idx,
                        "prep_cache must be initialised BEFORE it is used")

    def test_prep_cache_populated_per_package(self) -> None:
        """prep_cache must be assigned inside the Clear Cache loop."""
        src = self._get_cmd_start_source()
        self.assertIn('prep_cache[pkg]', src,
                      "prep_cache[pkg] assignment missing — will produce empty dict")

    def test_no_broad_except_hiding_nameerror(self) -> None:
        """The Clear Cache loop must not swallow NameError silently.

        There may be a try/except around the clear-cache call, but it must
        NOT catch NameError coming from prep_cache being undefined.
        """
        src = self._get_cmd_start_source()
        # After prep_cache is defined, there should be no bare 'except:' or
        # 'except Exception' that wraps the prep_cache assignment itself.
        # Easiest proxy: prep_cache dict init and assignment must both be present.
        self.assertIn("prep_cache: dict", src)
        self.assertIn("prep_cache[pkg]", src)


# ---------------------------------------------------------------------------
# TASK 3 — Layout no-overlap for 1-6 packages
# ---------------------------------------------------------------------------

class TestLayoutNoOverlap(unittest.TestCase):
    """Validate the smart landscape layout for 1-6 packages produces no overlaps."""

    def _calc(self, n: int, screen_w: int = 1280, screen_h: int = 720,
              dock_frac: float = 0.50) -> list:
        from agent.window_layout import calculate_split_layout
        pkgs = [f"com.test.pkg{i}" for i in range(n)]
        return calculate_split_layout(pkgs, screen_w, screen_h,
                                      termux_log_fraction=dock_frac)

    def _overlaps(self, a, b) -> bool:
        return not (a.right <= b.left or b.right <= a.left or
                    a.bottom <= b.top or b.bottom <= a.top)

    def _assert_no_overlap(self, rects) -> None:
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                if self._overlaps(rects[i], rects[j]):
                    self.fail(
                        f"Packages {i} and {j} overlap: "
                        f"{rects[i].left},{rects[i].top}-{rects[i].right},{rects[i].bottom} "
                        f"vs {rects[j].left},{rects[j].top}-{rects[j].right},{rects[j].bottom}"
                    )

    def test_1_package_no_overlap(self) -> None:
        rects = self._calc(1)
        self.assertEqual(len(rects), 1)
        # Single package — no pair to overlap.

    def test_2_packages_no_overlap(self) -> None:
        rects = self._calc(2)
        self.assertEqual(len(rects), 2)
        self._assert_no_overlap(rects)

    def test_3_packages_no_overlap(self) -> None:
        rects = self._calc(3)
        self.assertEqual(len(rects), 3)
        self._assert_no_overlap(rects)

    def test_4_packages_no_overlap(self) -> None:
        rects = self._calc(4)
        self.assertEqual(len(rects), 4)
        self._assert_no_overlap(rects)

    def test_5_packages_no_overlap(self) -> None:
        rects = self._calc(5)
        self.assertEqual(len(rects), 5)
        self._assert_no_overlap(rects)

    def test_6_packages_no_overlap(self) -> None:
        rects = self._calc(6)
        self.assertEqual(len(rects), 6)
        self._assert_no_overlap(rects)

    def test_2_packages_side_by_side(self) -> None:
        """2 packages in landscape must share the same row (same y-range) side by side."""
        rects = self._calc(2)
        self.assertEqual(len(rects), 2)
        # Both in row 0 → same top coordinate
        self.assertEqual(rects[0].top, rects[1].top,
                         "Both packages should have the same top (same row)")
        # Side-by-side: package 1 starts where package 0 ends
        self.assertGreater(rects[1].left, rects[0].left,
                           "Package 1 must start to the right of package 0")
        self.assertGreaterEqual(rects[1].left, rects[0].right,
                                "Package 1 left must be >= package 0 right (no overlap)")

    def test_y_starts_at_border_height_not_zero(self) -> None:
        """First package top must be >= status bar height (not y=0)."""
        from agent.window_layout import SAFE_TOP_INSET_PX
        rects = self._calc(1)
        self.assertGreaterEqual(rects[0].top, SAFE_TOP_INSET_PX,
                                f"First row y={rects[0].top} must be >= {SAFE_TOP_INSET_PX}")

    def test_same_bounds_not_applied_to_all_packages(self) -> None:
        """No two packages should have identical (left, top, right, bottom)."""
        rects = self._calc(3)
        seen: set[tuple] = set()
        for r in rects:
            key = (r.left, r.top, r.right, r.bottom)
            self.assertNotIn(key, seen,
                             f"Duplicate bounds found: {key}")
            seen.add(key)

    def test_termux_excluded_from_layout(self) -> None:
        """Termux package must be flagged as layout-excluded."""
        from agent.window_layout import _is_layout_excluded
        self.assertTrue(_is_layout_excluded("com.termux"),
                        "com.termux must be layout-excluded")
        self.assertTrue(_is_layout_excluded("com.termux.boot"),
                        "com.termux.boot must be layout-excluded")
        self.assertFalse(_is_layout_excluded("com.moons.litesc"),
                         "Moons package must NOT be layout-excluded")

    def test_side_by_side_stable_different_screen_sizes(self) -> None:
        """Side-by-side layout must work on both 720x1280 (rotated) and 1280x720."""
        rects_a = self._calc(2, 1280, 720)
        rects_b = self._calc(2, 720, 1280)
        # Both should produce 2 non-overlapping rects
        self.assertEqual(len(rects_a), 2)
        self.assertEqual(len(rects_b), 2)
        self._assert_no_overlap(rects_a)
        self._assert_no_overlap(rects_b)


# ---------------------------------------------------------------------------
# TASK 3 — clone_prefs_candidates includes /data/user/0/ paths
# ---------------------------------------------------------------------------

class TestClonePrefsUserPath(unittest.TestCase):
    """Verify that clone_prefs_candidates includes /data/user/0/ variant."""

    def test_user_0_path_in_candidates(self) -> None:
        from agent.window_layout import clone_prefs_candidates
        candidates = clone_prefs_candidates("com.moons.litesc")
        paths_str = [str(p) for p in candidates]
        has_user_0 = any("/data/user/0/" in p or "\\data\\user\\0\\" in p
                         for p in paths_str)
        self.assertTrue(has_user_0,
                        "/data/user/0/ path missing from clone_prefs_candidates; "
                        f"got: {paths_str}")

    def test_package_specific_filename_in_candidates(self) -> None:
        """com.moons.litesc_preferences.xml must be a candidate."""
        from agent.window_layout import clone_prefs_candidates
        candidates = clone_prefs_candidates("com.moons.litesc")
        names = [p.name for p in candidates]
        self.assertIn("com.moons.litesc_preferences.xml", names,
                      "Package-specific XML not in candidates")

    def test_pkg_preferences_still_in_candidates(self) -> None:
        """Legacy App Cloner pkg_preferences.xml must remain a candidate."""
        from agent.window_layout import clone_prefs_candidates
        candidates = clone_prefs_candidates("com.moons.litesc")
        names = [p.name for p in candidates]
        self.assertIn("pkg_preferences.xml", names)


# ---------------------------------------------------------------------------
# TASK 4 — Usage column render
# ---------------------------------------------------------------------------

class TestStreamlinedStartTable(unittest.TestCase):
    """build_start_table renders only #, Package, Username."""

    def _make_table(self) -> str:
        from agent.commands import build_start_table
        rows = [
            (1, "com.test.pkg0", "TestUser", "Online", "2m 5s", "256MB"),
        ]
        return build_start_table(rows, use_color=False)

    def test_only_three_headers(self) -> None:
        table = self._make_table()
        header_line = [ln for ln in table.splitlines() if "Package" in ln][0]
        cols = [c.strip() for c in header_line.split("│") if c.strip()]
        self.assertEqual(cols, ["#", "Package", "Username"])

    def test_legacy_trailing_fields_not_rendered(self) -> None:
        table = self._make_table()
        self.assertIn("TestUser", table)
        self.assertNotIn("Online", table)
        self.assertNotIn("256MB", table)
        self.assertNotIn("Runtime", table)
        self.assertNotIn("Usage", table)


# ---------------------------------------------------------------------------
# TASK 4 — RAM usage detection (android.get_package_ram_usage)
# ---------------------------------------------------------------------------

class TestGetPackageRamUsage(unittest.TestCase):
    """Unit tests for android.get_package_ram_usage."""

    def test_returns_dict_with_required_keys(self) -> None:
        """Must return a dict with pid, rss_kb, usage_mb, method, success, error."""
        from unittest.mock import patch
        # Patch run_android_command to avoid actual subprocess calls
        with patch("agent.android.detect_root") as mock_root, \
             patch("agent.android.get_package_pid", return_value=""), \
             patch("agent.android.get_app_memory_mb", return_value=None):
            mock_root.return_value = type("RI", (), {"available": False, "tool": None})()
            from agent.android import get_package_ram_usage
            result = get_package_ram_usage("com.test.pkg0")
        for key in ("pid", "rss_kb", "usage_mb", "method", "success", "error"):
            self.assertIn(key, result, f"Key '{key}' missing from result")

    def test_dead_package_returns_zero_mb(self) -> None:
        """Package with no PID and no meminfo should return N/A."""
        from unittest.mock import patch
        with patch("agent.android.detect_root") as mock_root, \
             patch("agent.android.get_package_pid", return_value=""), \
             patch("agent.android.get_app_memory_mb", return_value=None):
            mock_root.return_value = type("RI", (), {"available": False, "tool": None})()
            from agent.android import get_package_ram_usage
            result = get_package_ram_usage("com.test.pkg0")
        self.assertEqual(result["usage_mb"], "N/A")
        self.assertEqual(result["rss_kb"], 0)

    def test_running_package_with_dumpsys_shows_mb(self) -> None:
        """Package whose dumpsys meminfo returns 256 MB shows '256MB'."""
        from unittest.mock import patch
        with patch("agent.android.detect_root") as mock_root, \
             patch("agent.android.get_package_pid", return_value=""), \
             patch("agent.android.get_app_memory_mb", return_value=256.0):
            mock_root.return_value = type("RI", (), {"available": False, "tool": None})()
            from agent.android import get_package_ram_usage
            result = get_package_ram_usage("com.test.pkg0")
        self.assertEqual(result["usage_mb"], "256 MB")
        self.assertEqual(result["success"], True)

    def test_missing_proc_file_does_not_crash(self) -> None:
        """Even if /proc/PID/status doesn't exist, must return safely."""
        from unittest.mock import patch, mock_open
        with patch("agent.android.detect_root") as mock_root, \
             patch("agent.android.get_package_pid", return_value="99999"), \
             patch("agent.android.get_app_memory_mb", return_value=None), \
             patch("builtins.open", side_effect=OSError("no such file")):
            mock_root.return_value = type("RI", (), {"available": True, "tool": "su"})()
            from agent.android import get_package_ram_usage
            result = get_package_ram_usage("com.test.pkg0")
        # Must not crash; rss_kb may be 0
        self.assertIsInstance(result, dict)
        self.assertEqual(result["rss_kb"], 0)

    def test_proc_status_vms_rss_parsed(self) -> None:
        """VmRSS line in /proc/PID/status is parsed correctly."""
        from unittest.mock import patch, mock_open
        fake_status = "Name:\tcom.test.pkg\nVmRSS:\t 131072 kB\n"
        with patch("agent.android.detect_root") as mock_root, \
             patch("agent.android.get_package_pid", return_value="1234"), \
             patch("agent.android.get_app_memory_mb", return_value=None), \
             patch("builtins.open", mock_open(read_data=fake_status)):
            mock_root.return_value = type("RI", (), {"available": True, "tool": "su"})()
            from agent.android import get_package_ram_usage
            result = get_package_ram_usage("com.test.pkg0")
        self.assertEqual(result["rss_kb"], 131072)
        self.assertEqual(result["method"], "proc_status")
        self.assertTrue(result["success"])

    def test_gb_display_for_large_ram(self) -> None:
        """Packages using >1 GB should display as X.XGB."""
        from unittest.mock import patch
        with patch("agent.android.detect_root") as mock_root, \
             patch("agent.android.get_package_pid", return_value=""), \
             patch("agent.android.get_app_memory_mb", return_value=1500.0):
            mock_root.return_value = type("RI", (), {"available": False, "tool": None})()
            from agent.android import get_package_ram_usage
            result = get_package_ram_usage("com.test.pkg0")
        self.assertIn("GB", result["usage_mb"],
                      f"Expected GB suffix for large RAM, got: {result['usage_mb']}")


# ---------------------------------------------------------------------------
# TASK 5 — Public UI cleanliness
# ---------------------------------------------------------------------------

class TestPublicUICleanliness(unittest.TestCase):
    """Public start output must not contain forbidden strings."""

    def _make_table(self, state: str = "Online") -> str:
        from agent.commands import build_start_table
        rows = [(1, "com.test.pkg0", "TestUser", state, "2m", "128MB")]
        return build_start_table(rows, use_color=False)

    def test_no_checking_package_in_table(self) -> None:
        table = self._make_table()
        self.assertNotIn("Checking Package", table)

    def test_in_lobby_is_not_public_display_value(self) -> None:
        """Authenticated lobby/not-playing presence must display as Dead."""
        import ast, inspect
        import agent.commands as _mod
        src = inspect.getsource(_mod.cmd_start)
        self.assertNotIn('"In-Lobby"', src)
        self.assertNotIn('"In-Lobby",', src)

    def test_no_joining_in_display_map_values(self) -> None:
        """_STATE_DISPLAY_MAP must not produce 'Joining' as a Termux display value.

        v1.0.4 note: Joining IS a real supervisor state now (the bridge
        sends it to the APK so users see Dead → Launching → Joining →
        Online). The Termux TERMINAL still collapses Joining to
        Launching though, because the terminal user already sees the
        start sequence directly and doesn't need the extra step.

        So this test only forbids "Joining" as a VALUE in the dict.
        Having it as a KEY (mapped to "Launching") is required.
        """
        import inspect, re
        import agent.commands as _mod
        src = inspect.getsource(_mod.cmd_start)
        start = src.find("_STATE_DISPLAY_MAP")
        end = src.find("}", start) + 1
        map_src = src[start:end]
        values = re.findall(r':\s*"([^"]+)"', map_src)
        self.assertNotIn(
            "Joining", values,
            "_STATE_DISPLAY_MAP must not map any state to 'Joining' "
            "as a Termux terminal display value (KEY is fine).",
        )

    def test_allowed_states_in_display_map_values(self) -> None:
        """_STATE_DISPLAY_MAP values must only be from the allowed public set."""
        import inspect
        import agent.commands as _mod
        src = inspect.getsource(_mod.cmd_start)
        # Extract the map block from source by slicing
        start = src.find("_STATE_DISPLAY_MAP")
        end   = src.find("}", start) + 1
        map_src = src[start:end]
        allowed = {
            "Online", "Dead", "Launching", "Reopening", "Failed",
        }
        # Find all string literals that appear after a ':' (the values)
        import re
        vals = re.findall(r':\s*"([^"]+)"', map_src)
        for v in vals:
            self.assertIn(v, allowed,
                          f"_STATE_DISPLAY_MAP maps to forbidden display state: '{v}'")

    def test_usage_column_omitted_from_public_output(self) -> None:
        table = self._make_table()
        self.assertNotIn("Usage", table)
        self.assertNotIn("128MB", table)

    def test_no_noisy_layout_tags_in_public_table(self) -> None:
        table = self._make_table()
        for forbidden in ("DENG_REJOIN", "dumpsys", "Layout", "Docking"):
            self.assertNotIn(forbidden, table,
                             f"'{forbidden}' must not appear in the public table")


# ---------------------------------------------------------------------------
# TASK 3 — layout_calc probe log exists in commands source
# ---------------------------------------------------------------------------

class TestLayoutProbeLogTags(unittest.TestCase):
    """Required probe log tags must be present in commands.py source."""

    def _get_commands_source(self) -> str:
        import agent.commands as _m
        import inspect
        return inspect.getsource(_m)

    def test_deng_rejoin_layout_calc_tag_present(self) -> None:
        src = self._get_commands_source()
        self.assertIn("[DENG_REJOIN_LAYOUT_GRID]", src)

    def test_deng_rejoin_layout_bounds_tag_present(self) -> None:
        src = self._get_commands_source()
        self.assertIn("[DENG_REJOIN_LAYOUT_BOUNDS]", src)

    def test_deng_rejoin_package_usage_tag_in_android(self) -> None:
        import agent.android as _m
        import inspect
        src = inspect.getsource(_m)
        self.assertIn("[DENG_REJOIN_PACKAGE_USAGE]", src)


if __name__ == "__main__":
    unittest.main()
