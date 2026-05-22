"""Regression tests for Bug 3 (probe ``p-52aeb6420f``).

Symptom on real device (SM-N9810, Android 10) when running with 3
selected packages:

    All Roblox windows appeared to land in the SAME slot (row 1 col 2).

Root cause analysis: the supervisor's ``_reapply_layout_for_package(pkg)``
helper — invoked from every per-package relaunch (Dead / No Heartbeat /
RAM restart) — used to call ``calculate_split_layout([pkg], …)``.  That
collapsed the multi-package grid into a 1-package right-pane layout,
overwriting the original deterministic slot the package was assigned at
Start time.  Combined with Bug 1 (RAM-restart loop) this caused every
package to be re-laid-out into the same single-package slot on every
restart cycle — exactly the "all-row-1-col-2" symptom.

Fix: ``_reapply_layout_for_package`` now first consults the
``cfg["last_layout_preview"]`` (or ``cfg["_layout_rects"]``) saved by
``_prepare_automatic_layout`` and reuses the original slot rect for
*that specific package*.  The 1-package fallback is only used when no
stored layout exists (cold supervisor, single-package installs).
"""
from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock


_STORED_LAYOUT = [
    {"package": "com.moons.litesc", "left": 426, "top": 25,  "right": 852,  "bottom": 256},
    {"package": "com.moons.litesd", "left": 852, "top": 25,  "right": 1280, "bottom": 256},
    {"package": "com.moons.litese", "left": 426, "top": 256, "right": 852,  "bottom": 487},
]


class TestBug3StoredSlotPreserved(unittest.TestCase):

    def _patched_apply(self):
        """Patch the apply layer + force_resize and return their mocks."""
        return (
            patch("agent.window_apply.apply_window_layout_silent"),
            patch("agent.window_apply.force_resize_package", return_value=(True, "ok")),
            patch("agent.supervisor.load_config"),
        )

    def test_load_stored_rect_returns_package_specific_rect(self):
        from agent.supervisor import _load_stored_rect_for_package
        cfg = {"last_layout_preview": list(_STORED_LAYOUT)}

        a = _load_stored_rect_for_package(cfg, "com.moons.litesc")
        b = _load_stored_rect_for_package(cfg, "com.moons.litesd")
        c = _load_stored_rect_for_package(cfg, "com.moons.litese")

        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertIsNotNone(c)
        self.assertEqual((a.left, a.top, a.right, a.bottom), (426, 25,  852,  256))
        self.assertEqual((b.left, b.top, b.right, b.bottom), (852, 25,  1280, 256))
        self.assertEqual((c.left, c.top, c.right, c.bottom), (426, 256, 852,  487))
        # Each package keeps a UNIQUE slot.
        seen = {(r.left, r.top, r.right, r.bottom) for r in (a, b, c)}
        self.assertEqual(len(seen), 3)

    def test_load_stored_rect_returns_none_when_package_missing(self):
        from agent.supervisor import _load_stored_rect_for_package
        cfg = {"last_layout_preview": list(_STORED_LAYOUT)}
        self.assertIsNone(
            _load_stored_rect_for_package(cfg, "com.not.in.layout")
        )

    def test_load_stored_rect_returns_none_when_no_layout_saved(self):
        from agent.supervisor import _load_stored_rect_for_package
        self.assertIsNone(
            _load_stored_rect_for_package({}, "com.moons.litesc")
        )

    def test_reapply_uses_stored_slot_for_each_package(self):
        """force_resize_package is called with the package's ORIGINAL slot."""
        from agent.supervisor import _reapply_layout_for_package

        cfg = {
            "last_layout_preview": list(_STORED_LAYOUT),
            "_layout_rects": list(_STORED_LAYOUT),
            "screen_mode": "landscape",
        }

        with patch("agent.supervisor.load_config", return_value=cfg), \
             patch("agent.window_apply.apply_window_layout_silent") as mock_silent, \
             patch("agent.window_apply.force_resize_package",
                   return_value=(True, "ok")) as mock_force, \
             patch("agent.window_layout.detect_display_info") as mock_detect:

            mock_detect.return_value = MagicMock(width=1280, height=720, density=164)

            # Reapply for each package and verify the rect passed to
            # force_resize matches that package's stored slot.
            expected = {
                "com.moons.litesc": (426, 25,  852,  256),
                "com.moons.litesd": (852, 25,  1280, 256),
                "com.moons.litese": (426, 256, 852,  487),
            }
            for pkg, exp in expected.items():
                mock_force.reset_mock()
                _reapply_layout_for_package(pkg)
                self.assertEqual(mock_force.call_count, 1,
                                 f"force_resize_package not called for {pkg}")
                _args, _kwargs = mock_force.call_args
                rect = _args[1]
                self.assertEqual(
                    (rect.left, rect.top, rect.right, rect.bottom), exp,
                    f"{pkg} re-applied to wrong slot",
                )

            # apply_window_layout_silent was called once per package too.
            self.assertEqual(mock_silent.call_count, 3)

    def test_reapply_falls_back_when_no_stored_layout(self):
        """Cold supervisor with no stored layout → single-package fallback runs."""
        from agent.supervisor import _reapply_layout_for_package
        from agent.window_layout import WindowRect

        fallback_rect = WindowRect(
            package="com.solo.pkg", left=0, top=0, right=1280, bottom=720,
        )

        with patch("agent.supervisor.load_config", return_value={}), \
             patch("agent.window_layout.calculate_split_layout",
                   return_value=[fallback_rect]) as mock_calc, \
             patch("agent.window_layout.detect_display_info") as mock_detect, \
             patch("agent.window_apply.apply_window_layout_silent"), \
             patch("agent.window_apply.force_resize_package",
                   return_value=(True, "ok")) as mock_force:

            mock_detect.return_value = MagicMock(width=1280, height=720, density=164)
            _reapply_layout_for_package("com.solo.pkg")

            mock_calc.assert_called_once()
            mock_force.assert_called_once()

    def test_relaunch_does_not_recompute_layout_for_other_packages(self):
        """Reapply for ONE package must not touch other packages' slots."""
        from agent.supervisor import _reapply_layout_for_package

        cfg = {
            "last_layout_preview": list(_STORED_LAYOUT),
            "screen_mode": "landscape",
        }
        captured_packages: list[str] = []

        def _capture(rects, **_kwargs):
            for r in rects:
                captured_packages.append(r.package)

        with patch("agent.supervisor.load_config", return_value=cfg), \
             patch("agent.window_apply.apply_window_layout_silent",
                   side_effect=_capture), \
             patch("agent.window_apply.force_resize_package",
                   return_value=(True, "ok")):
            _reapply_layout_for_package("com.moons.litesc")

        # Only litesc — not litesd or litese.
        self.assertEqual(captured_packages, ["com.moons.litesc"])

    def test_reapply_emits_probe_event(self):
        """Bug 3 fix must be observable via [DENG_REJOIN_REAPPLY_LAYOUT]."""
        from agent.supervisor import _reapply_layout_for_package

        cfg = {"last_layout_preview": list(_STORED_LAYOUT)}
        captured_messages: list[str] = []

        class _CapturingLogger:
            def info(self, fmt, *args, **_kwargs):
                try:
                    captured_messages.append(fmt % args if args else fmt)
                except TypeError:
                    captured_messages.append(str(fmt))
            def debug(self, *_a, **_kw): pass
            def warning(self, *_a, **_kw): pass
            def error(self, *_a, **_kw): pass

        cap_logger = _CapturingLogger()

        with patch("agent.supervisor.load_config", return_value=cfg), \
             patch("logging.getLogger", return_value=cap_logger), \
             patch("agent.window_apply.apply_window_layout_silent"), \
             patch("agent.window_apply.force_resize_package",
                   return_value=(True, "ok")):
            _reapply_layout_for_package("com.moons.litesc")

        self.assertTrue(
            any("[DENG_REJOIN_REAPPLY_LAYOUT]" in m for m in captured_messages),
            f"Expected [DENG_REJOIN_REAPPLY_LAYOUT] in {captured_messages}",
        )
        self.assertTrue(
            any("rect_source=stored_slot" in m for m in captured_messages),
            "Expected rect_source=stored_slot in probe event",
        )


# ── Variety of package counts — every slot stays unique ──────────────────────


class TestBug3DeterministicSlotsAcrossPackageCounts(unittest.TestCase):
    """The original Start path must always produce unique per-package slots."""

    def _slots_for(self, n: int, screen_mode: str = "landscape"):
        from agent import window_layout
        packages = [f"com.test.pkg{i}" for i in range(n)]
        rects = window_layout.calculate_split_layout(
            packages, 1280, 720, termux_log_fraction=0.0, screen_mode=screen_mode,
        )
        return [(r.package, r.left, r.top, r.right, r.bottom) for r in rects]

    def test_two_packages_get_unique_slots(self):
        slots = self._slots_for(2)
        self.assertEqual(len(slots), 2)
        bounds = {(l, t, r, b) for (_, l, t, r, b) in slots}
        self.assertEqual(len(bounds), 2, f"duplicate slots: {slots}")

    def test_three_packages_get_unique_slots(self):
        slots = self._slots_for(3)
        self.assertEqual(len(slots), 3)
        bounds = {(l, t, r, b) for (_, l, t, r, b) in slots}
        self.assertEqual(len(bounds), 3, f"duplicate slots: {slots}")

    def test_four_packages_get_unique_slots(self):
        slots = self._slots_for(4)
        self.assertEqual(len(slots), 4)
        bounds = {(l, t, r, b) for (_, l, t, r, b) in slots}
        self.assertEqual(len(bounds), 4)

    def test_six_packages_get_unique_slots(self):
        slots = self._slots_for(6)
        self.assertEqual(len(slots), 6)
        bounds = {(l, t, r, b) for (_, l, t, r, b) in slots}
        self.assertEqual(len(bounds), 6)

    def test_nine_packages_get_unique_slots(self):
        slots = self._slots_for(9)
        self.assertEqual(len(slots), 9)
        bounds = {(l, t, r, b) for (_, l, t, r, b) in slots}
        self.assertEqual(len(bounds), 9)

    def test_three_landscape_matches_probe_p52aeb6420f_pattern(self):
        """The three packages from the probe MUST resolve to three distinct slots."""
        slots = self._slots_for(3, "landscape")
        bounds = {(l, t, r, b) for (_, l, t, r, b) in slots}
        # No package gets the same rect (the symptom from the bug report).
        self.assertEqual(len(bounds), 3, slots)


if __name__ == "__main__":
    unittest.main()
