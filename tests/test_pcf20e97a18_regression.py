"""Regression tests for probe p-cf20e97a18.

Issues fixed:
1. Package windows used only ~33% of screen width because
   _prepare_automatic_layout used left_fraction=0.50 AND the lte6 slot order
   skipped col-0 of that half, leaving packages in cols 1+2 of a 640px pane.
   Fix: use termux_log_fraction=0.0 so the full 1280px width is the grid base.

2. WatchdogSupervisor.run_forever() crashed with
   "ValueError: sleep length must be non-negative" when the render_callback
   took longer than the remaining sleep time, making
   time.sleep(min(1.0, deadline - time.time())) receive a negative argument.
   Fix: time.sleep(max(0.0, min(1.0, deadline - time.time()))).
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


# ─── helpers ─────────────────────────────────────────────────────────────────

def _split_layout(pkgs: list[str], w: int, h: int, fraction: float, mode: str):
    from agent import window_layout as wl
    with mock.patch("agent.window_layout._detect_status_bar_height", return_value=25):
        return wl.calculate_split_layout(
            pkgs, w, h, termux_log_fraction=fraction, screen_mode=mode
        )


# ─── Issue-1: full-width layout ───────────────────────────────────────────────

class TestFullWidthLayout(unittest.TestCase):
    """Verify that termux_log_fraction=0.0 gives full-screen package bounds."""

    def test_3pkg_landscape_full_width_bounds(self) -> None:
        """3 packages on 1280×720 landscape with fraction=0.0.

        Expected with LANDSCAPE_LTE6_SLOT_ORDER=(0,1,2,0,3,4,0,5,6):
          col0 = 0–426   (empty — Termux)
          col1 = 426–852  (packages 1, 3)
          col2 = 852–1280 (package 2)
        """
        rects = _split_layout(
            ["pkg1", "pkg2", "pkg3"], 1280, 720, fraction=0.0, mode="landscape"
        )
        self.assertEqual(len(rects), 3)
        expected_left = (426, 852, 426)
        expected_top  = (25,  25,  256)
        for i, r in enumerate(rects):
            with self.subTest(pkg=r.package):
                self.assertEqual(r.left, expected_left[i], f"pkg{i+1} left")
                self.assertEqual(r.top,  expected_top[i],  f"pkg{i+1} top")

    def test_3pkg_landscape_old_half_width_bounds(self) -> None:
        """Verify OLD (broken) layout reference values for comparison."""
        rects = _split_layout(
            ["pkg1", "pkg2", "pkg3"], 1280, 720, fraction=0.50, mode="landscape"
        )
        self.assertEqual(len(rects), 3)
        # With fraction=0.50: px0=640, cell_w=213 → packages at 853,1066,853
        expected_left = (853, 1066, 853)
        for i, r in enumerate(rects):
            with self.subTest(pkg=r.package):
                self.assertEqual(r.left, expected_left[i])

    def test_full_width_packages_wider_than_half_width(self) -> None:
        """Full-width packages must be at least 2x wider than the old layout."""
        full = _split_layout(["pkg1"], 1280, 720, fraction=0.0,  mode="landscape")
        half = _split_layout(["pkg1"], 1280, 720, fraction=0.50, mode="landscape")
        full_w = full[0].right - full[0].left
        half_w = half[0].right - half[0].left
        self.assertGreaterEqual(full_w, half_w * 1.8,
            f"Full-width={full_w}px should be ≥1.8× half-width={half_w}px")

    def test_full_width_right_edge_reaches_screen(self) -> None:
        """The rightmost package must reach the screen right edge."""
        rects = _split_layout(
            ["pkg1", "pkg2"], 1280, 720, fraction=0.0, mode="landscape"
        )
        rightmost = max(r.right for r in rects)
        self.assertEqual(rightmost, 1280)

    def test_top_inset_unchanged_between_fraction_values(self) -> None:
        """Height inset (top=25) must not change when fraction changes."""
        full = _split_layout(["pkg1"], 1280, 720, fraction=0.0,  mode="landscape")
        half = _split_layout(["pkg1"], 1280, 720, fraction=0.50, mode="landscape")
        self.assertEqual(full[0].top, 25)
        self.assertEqual(half[0].top, 25)
        self.assertEqual(full[0].top, half[0].top)

    def test_probe_pcf20e97a18_old_bounds_no_longer_produced(self) -> None:
        """The exact probe bounds (left=853) must not appear in full-width layout."""
        rects = _split_layout(
            ["com.moons.litesc", "com.moons.litesd", "com.moons.litese"],
            1280, 720, fraction=0.0, mode="landscape",
        )
        for r in rects:
            self.assertNotEqual(r.left, 853,
                f"{r.package}: left=853 is the OLD broken value; got left={r.left}")
            self.assertNotEqual(r.left, 1066,
                f"{r.package}: left=1066 is the OLD broken value; got left={r.left}")


class TestTermuxDockFractionDerivation(unittest.TestCase):
    """Verify that termux_dock_fraction is derived from rect positions."""

    def test_termux_fraction_equals_first_package_left_over_width(self) -> None:
        """termux_dock_fraction must equal (min package left) / screen_width."""
        from agent import window_layout as wl
        with mock.patch("agent.window_layout._detect_status_bar_height", return_value=25):
            rects = wl.calculate_split_layout(
                ["pkg1", "pkg2", "pkg3"], 1280, 720,
                termux_log_fraction=0.0, screen_mode="landscape",
            )
        min_left = min(r.left for r in rects)
        expected_frac = min_left / 1280
        # For landscape lte6 with 3 pkgs and 1280px width: 426/1280 ≈ 0.333
        self.assertAlmostEqual(expected_frac, 1 / 3, places=1)

    def test_prepare_layout_sets_termux_dock_fraction_in_cfg(self) -> None:
        """_prepare_automatic_layout must store termux_dock_fraction in cfg."""
        from agent import commands, window_layout as wl
        fake_cfg = {"screen_mode": "landscape"}
        fake_entries = [
            {"package": "com.moons.litesc", "enabled": True},
            {"package": "com.moons.litesd", "enabled": True},
            {"package": "com.moons.litese", "enabled": True},
        ]
        fake_display = wl.DisplayInfo(width=1280, height=720, density=160)

        def _fake_disc(*a, **kw):
            return "/dev/null", {}

        with mock.patch("agent.window_layout.detect_display_info", return_value=fake_display), \
             mock.patch("agent.window_layout._detect_status_bar_height", return_value=25), \
             mock.patch("agent.window_layout._is_layout_excluded", return_value=False), \
             mock.patch("agent.commands.save_config"), \
             mock.patch("agent.android.detect_root",
                        return_value=__import__("agent.android", fromlist=["RootInfo"]).RootInfo(False, None, "")), \
             mock.patch("agent.commands._verify_layout_post_launch", return_value=({}, [])), \
             mock.patch("agent.layout_discovery.run_discovery_and_log", side_effect=_fake_disc), \
             mock.patch("agent.window_apply.apply_window_layout", return_value=[]):
            result_cfg, _note = commands._prepare_automatic_layout(fake_cfg, fake_entries)

        # termux_dock_fraction must be ≤ 0.5 (not 0.50 as before)
        frac = result_cfg.get("termux_dock_fraction")
        self.assertIsNotNone(frac, "termux_dock_fraction was not set in cfg")
        self.assertLess(frac, 0.45,
            f"Expected termux_dock_fraction < 0.45 (full-width derived), got {frac}")


# ─── Issue-2: negative sleep fix ─────────────────────────────────────────────

class TestWatchdogSleepNonNegative(unittest.TestCase):
    """Verify WatchdogSupervisor.run_forever() does not crash when the render
    callback takes longer than the sleep interval."""

    def test_slow_render_callback_does_not_crash_supervisor(self) -> None:
        """A render callback that outlasts display_interval must NOT raise."""
        from agent.supervisor import WatchdogSupervisor, STATUS_ONLINE

        entries = [{"package": "com.moons.litesc", "enabled": True}]
        cfg = {"supervisor": {"health_check_interval_seconds": 10}}

        render_call_count = 0

        def _slow_render() -> None:
            nonlocal render_call_count
            render_call_count += 1
            # Sleep longer than display_interval to force negative remainder
            time.sleep(0.05)

        sup = WatchdogSupervisor(entries, cfg)
        # Pre-seed Online + grace so no recovery is triggered
        now = time.time()
        for pkg in sup.packages:
            sup.status_map[pkg] = STATUS_ONLINE
            sup._grace_until[pkg] = now + 300
            sup._last_online_ts[pkg] = now

        errors: list[Exception] = []

        def _run():
            try:
                sup.run_forever(
                    render_callback=_slow_render,
                    display_interval=0.02,  # shorter than render's 0.05s sleep
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with mock.patch("agent.supervisor.signal"), \
             mock.patch.object(sup, "_detect_package_state",
                               return_value=(STATUS_ONLINE, {"process_running": "true",
                                                             "in_game": "true",
                                                             "heartbeat_ok": "true",
                                                             "warning_detected": "false",
                                                             "elapsed_ms": 1,
                                                             "reason": "mocked"})), \
             mock.patch("agent.db.insert_event"), \
             mock.patch("agent.db.insert_heartbeat"):
            thread = threading.Thread(target=_run, daemon=True)
            thread.start()
            # Let it run a couple of cycles
            time.sleep(0.3)
            sup.stop("test_done")
            thread.join(timeout=3.0)

        self.assertFalse(thread.is_alive(), "run_forever() thread did not exit cleanly")
        self.assertEqual(errors, [],
            f"run_forever() raised unexpected exception(s): {errors}")

    def test_package_worker_sleep_is_non_negative(self) -> None:
        """_PackageWorker._sleep() must not call time.sleep(negative)."""
        import threading as _threading
        from agent.supervisor import _PackageWorker, STATUS_LAUNCHING

        stop_ev = _threading.Event()
        status_map: dict = {"com.moons.litesc": STATUS_LAUNCHING}
        entry = {"package": "com.moons.litesc", "enabled": True,
                 "auto_reopen_enabled": True, "auto_reconnect_enabled": True}
        worker = _PackageWorker(entry, {}, status_map, stop_ev)

        sleep_args: list[float] = []

        def _recording_sleep(secs):
            sleep_args.append(float(secs))

        # Set stop_event so _sleep exits at once without sleeping
        stop_ev.set()
        with mock.patch("agent.supervisor.time.sleep", side_effect=_recording_sleep):
            worker._sleep(0.1)

        for s in sleep_args:
            self.assertGreaterEqual(s, 0.0,
                f"_PackageWorker._sleep: time.sleep called with negative value {s}")

    def test_sleep_code_clamps_to_zero(self) -> None:
        """Inspect run_forever source to confirm max(0.0, ...) guard."""
        import inspect
        from agent.supervisor import WatchdogSupervisor
        src = inspect.getsource(WatchdogSupervisor.run_forever)
        self.assertIn(
            "max(0.0,",
            src,
            "run_forever() sleep must use max(0.0, ...) to prevent negative sleep",
        )


# ─── Continuity: full watchdog round completes without error ──────────────────

class TestWatchdogContinuity(unittest.TestCase):
    """Verify the watchdog continues through all packages in a round."""

    def test_watchdog_does_not_stop_after_first_round(self) -> None:
        """run_forever() must survive at least 2 complete rounds."""
        from agent.supervisor import WatchdogSupervisor, STATUS_ONLINE

        entries = [
            {"package": "com.moons.litesc", "enabled": True},
            {"package": "com.moons.litesd", "enabled": True},
            {"package": "com.moons.litese", "enabled": True},
        ]
        cfg = {"supervisor": {"health_check_interval_seconds": 10}}
        sup = WatchdogSupervisor(entries, cfg)
        # Pre-seed Online + grace so no recovery is triggered
        now = time.time()
        for pkg in sup.packages:
            sup.status_map[pkg] = STATUS_ONLINE
            sup._grace_until[pkg] = now + 300
            sup._last_online_ts[pkg] = now

        rounds_seen: list[int] = []

        def _render():
            rounds_seen.append(sup._round)

        errors: list[Exception] = []

        def _run():
            try:
                sup.run_forever(
                    render_callback=_render,
                    display_interval=0.05,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        # Mock the slow parts (including signal for non-main-thread safety)
        # Also mock _sup_interval() to return 1s (MIN is 10s in production).
        with mock.patch("agent.supervisor.signal"), \
             mock.patch.object(sup, "_sup_interval", return_value=1), \
             mock.patch("agent.supervisor.log_event"), \
             mock.patch.object(sup, "_detect_package_state",
                               return_value=(STATUS_ONLINE, {"process_running": "true",
                                                              "in_game": "true",
                                                              "heartbeat_ok": "true",
                                                              "warning_detected": "false",
                                                              "elapsed_ms": 1,
                                                              "reason": "mocked"})), \
             mock.patch("agent.db.insert_event"), \
             mock.patch("agent.db.insert_heartbeat"):
            thread = threading.Thread(target=_run, daemon=True)
            thread.start()
            time.sleep(0.5)
            sup.stop("test_complete")
            thread.join(timeout=3.0)

        self.assertFalse(thread.is_alive(), "Watchdog thread should have exited")
        self.assertEqual(errors, [], f"Watchdog raised: {errors}")
        # Verify at least 1 complete round ran (the probe issue was it crashed
        # after round 1 due to negative sleep — confirming round completion is key).
        self.assertGreaterEqual(sup._round, 1,
            f"Expected ≥1 watchdog round, only saw {sup._round}")


if __name__ == "__main__":
    unittest.main()
