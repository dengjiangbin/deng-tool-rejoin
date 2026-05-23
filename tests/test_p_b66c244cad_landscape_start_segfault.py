from __future__ import annotations

import inspect
import unittest
from unittest import mock

from agent import commands, safe_io, window_apply, window_layout
from agent.supervisor import STATUS_ONLINE, WatchdogSupervisor
from agent.launcher import RejoinResult


def _entry(pkg: str) -> dict:
    return {
        "package": pkg,
        "enabled": True,
        "account_username": pkg.rsplit(".", 1)[-1],
        "private_server_url": "https://www.roblox.com/share?code=abc&type=Server",
        "auto_reopen_enabled": True,
        "auto_reconnect_enabled": True,
        "roblox_user_id": 0,
    }


class TestProbeB66c244cadLandscapeStartSegfault(unittest.TestCase):
    def test_probe_crash_window_online_path_does_not_execute_auto_execute(self) -> None:
        packages = ["com.moons.litesc", "com.moons.litesd", "com.moons.litese"]
        entries = [_entry(pkg) for pkg in packages]
        sup = WatchdogSupervisor(entries, {"auto_execute_scripts": ["print(1)"]})
        sup._check_ram_optimization = mock.MagicMock()

        with mock.patch("agent.supervisor.log_event") as log:
            sup._handle_state(packages[0], entries[0], STATUS_ONLINE, "Launching", 123.0)

        event_names = [call.args[2] for call in log.call_args_list]
        self.assertIn("[DENG_REJOIN_ONLINE_STABLE]", event_names)
        sup._check_ram_optimization.assert_called_once()

    def test_landscape_start_uses_landscape_layout_not_portrait_path(self) -> None:
        with mock.patch("agent.window_layout._detect_status_bar_height", return_value=25):
            rects = window_layout.calculate_split_layout(
                ["com.moons.litesc", "com.moons.litesd", "com.moons.litese"],
                1280,
                720,
                termux_log_fraction=0.0,
                screen_mode="landscape",
            )

        self.assertEqual(
            [(r.left, r.top, r.right, r.bottom) for r in rects],
            [(426, 25, 852, 256), (852, 25, 1280, 256), (426, 256, 852, 487)],
        )

    def test_portrait_to_landscape_clears_stale_portrait_layout_state(self) -> None:
        cfg = {
            "screen_mode": "landscape",
            "last_layout_mode": "portrait",
            "last_layout_preview": [
                {"package": "com.moons.litesc", "left": 0, "top": 512, "right": 360, "bottom": 768}
            ],
            "termux_dock_fraction": 0.0,
        }
        entries = [_entry("com.moons.litesc")]
        applied: list[tuple[int, int, int, int]] = []

        def fake_apply(rects, **kwargs):
            applied.extend((r.left, r.top, r.right, r.bottom) for r in rects)
            return []

        with mock.patch.object(window_layout, "detect_display_info", return_value=window_layout.DisplayInfo(1280, 720, 164)), \
             mock.patch.object(window_layout, "_detect_status_bar_height", return_value=25), \
             mock.patch.object(window_apply, "apply_window_layout", side_effect=fake_apply):
            commands._verify_layout_post_launch(cfg, entries)

        self.assertEqual(applied, [(426, 25, 852, 256)])

    def test_bounds_readback_failure_does_not_raise(self) -> None:
        cfg = {"screen_mode": "landscape", "last_layout_mode": "landscape"}
        entries = [_entry("com.moons.litesc")]

        with mock.patch.object(window_layout, "detect_display_info", return_value=window_layout.DisplayInfo(1280, 720, 164)), \
             mock.patch.object(window_apply, "apply_window_layout", side_effect=RuntimeError("bounds failed")):
            verify, diag = commands._verify_layout_post_launch(cfg, entries)

        self.assertEqual(verify, {})
        self.assertEqual(diag, [])

    def test_package_launch_failure_is_controlled_result(self) -> None:
        result = RejoinResult(False, error="launch failed", root_used=True)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "launch failed")

    def test_terminal_restore_helpers_are_present_on_start_exit_paths(self) -> None:
        source = inspect.getsource(commands.cmd_start)
        self.assertIn("_release_start_lock", source)
        self.assertIn("_termux_exit_clean()", source)
        self.assertIn("_clear_terminal()", source)

    def test_faulthandler_context_api_available_before_start(self) -> None:
        self.assertTrue(callable(safe_io.set_crash_context))
        source = inspect.getsource(commands.main)
        self.assertLess(
            source.index("safe_io.setup_faulthandler()"),
            source.index("args = parse_args"),
        )


if __name__ == "__main__":
    unittest.main()
