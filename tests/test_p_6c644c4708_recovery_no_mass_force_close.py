"""Regression: dead-account recovery must not mass force-close every app + Termux.

Probe p-6c644c4708.  Root cause: every recovery re-ran
``setup_freeform_capabilities()``, which re-wrote the global/secure freeform +
``force_resizable_activities`` flags.  Toggling those flags makes WindowManager
recreate the whole activity stack, force-closing every running app — including
Termux ("root bound window" mass close).  It recurred on every recovery because
the secure-namespace mirrors never read back as truthy, so the writes re-fired
forever.

Fix:
1. ``freeform_enable.setup_freeform_capabilities`` writes each (namespace, key)
   at most once per process (session guard).  Later calls are read-only no-ops.
2. ``window_apply.force_resize_package`` no longer calls
   ``setup_freeform_capabilities`` at all — recovery only does the per-task
   resize and never touches global window settings while clones are live.
"""

from __future__ import annotations

import unittest
from unittest import mock

from agent import android, freeform_enable, window_apply


_ALL_DISABLED = {
    "global:enable_freeform_support": "0",
    "global:force_resizable_activities": "0",
    "global:freeform_window_management": "0",
    "global:development_settings_enabled": "0",
}


class TestRecoveryNoMassForceClose(unittest.TestCase):
    def setUp(self) -> None:
        freeform_enable.reset_freeform_session_guard()

    def _run_setup_twice(self):
        captured: list[tuple[str, str, str]] = []

        def fake_get(ns, key):
            # Secure mirrors never read truthy on real devices — that is what
            # used to re-fire the WM recreate on every recovery.
            return _ALL_DISABLED.get(f"{ns}:{key}")

        def fake_put(ns, key, value, root_tool="su"):
            captured.append((ns, key, value))
            return True

        with mock.patch.object(freeform_enable, "_settings_get", fake_get), \
             mock.patch.object(freeform_enable, "_settings_put_root", fake_put), \
             mock.patch.object(
                 android, "detect_root",
                 return_value=android.RootInfo(True, "su", ""),
             ):
            freeform_enable.setup_freeform_capabilities()
            first_pass = list(captured)
            captured.clear()
            # Second call simulates the next dead-account recovery.
            freeform_enable.setup_freeform_capabilities()
            second_pass = list(captured)
        return first_pass, second_pass

    def test_second_setup_writes_nothing(self) -> None:
        first_pass, second_pass = self._run_setup_twice()
        self.assertTrue(first_pass, "first setup should write the disabled flags")
        # The recovery (second) call must perform ZERO settings writes, so the
        # WindowManager stack is never recreated and no app/Termux is closed.
        self.assertEqual(
            second_pass, [],
            f"recovery re-fired settings writes (mass force-close risk): {second_pass}",
        )

    def test_secure_keys_only_written_once(self) -> None:
        first_pass, _ = self._run_setup_twice()
        secure_writes = [(ns, k) for ns, k, _ in first_pass if ns == "secure"]
        # Each secure key written at most once even though it never reads truthy.
        self.assertEqual(len(secure_writes), len(set(secure_writes)))

    def test_force_resize_package_does_not_call_setup_freeform(self) -> None:
        # The recovery resize must NOT invoke setup_freeform_capabilities, which
        # is what re-toggled the global WM flags and mass-closed every app.
        from agent.window_layout import WindowRect
        with mock.patch.object(
            freeform_enable, "setup_freeform_capabilities",
        ) as setup_mock, mock.patch.object(
            window_apply, "_direct_resize_via_root",
            return_value=(True, "verified"),
        ), mock.patch.object(
            android, "detect_root",
            return_value=android.RootInfo(True, "su", ""),
        ):
            window_apply.force_resize_package(
                "com.example.clone",
                WindowRect(package="com.example.clone", left=0, top=0, right=400, bottom=300),
            )
        self.assertEqual(
            setup_mock.call_count, 0,
            "force_resize_package re-poked global freeform settings during recovery",
        )

    def test_force_resize_package_still_resizes_single_task(self) -> None:
        with mock.patch.object(
            window_apply, "_direct_resize_via_root",
            return_value=(True, "verified"),
        ) as m, mock.patch.object(
            android, "detect_root",
            return_value=android.RootInfo(True, "su", ""),
        ):
            from agent.window_layout import WindowRect
            ok, _ = window_apply.force_resize_package(
                "com.example.clone",
                WindowRect(package="com.example.clone", left=0, top=0, right=400, bottom=300),
            )
        self.assertTrue(ok)
        self.assertEqual(m.call_count, 1)

    def test_force_resize_package_recovery_skips_windowing_mode_flip(self) -> None:
        with mock.patch.object(
            window_apply, "_direct_resize_via_root",
            return_value=(True, "verified"),
        ) as m, mock.patch.object(
            android, "detect_root",
            return_value=android.RootInfo(True, "su", ""),
        ):
            from agent.window_layout import WindowRect
            window_apply.force_resize_package(
                "com.example.clone",
                WindowRect(package="com.example.clone", left=0, top=0, right=400, bottom=300),
                skip_windowing_mode_flip=True,
            )
        _args, kwargs = m.call_args
        self.assertTrue(kwargs.get("skip_windowing_mode_flip"))

    def test_reapply_layout_uses_recovery_safe_resize(self) -> None:
        from agent.supervisor import _reapply_layout_for_package
        from agent.window_layout import WindowRect
        stored = WindowRect(
            package="com.moons.litesc",
            left=0, top=0, right=400, bottom=300,
        )
        cfg = {
            "screen_mode": "landscape",
            "last_layout_preview": {
                "rects": [{
                    "package": "com.moons.litesc",
                    "left": 0, "top": 0, "right": 400, "bottom": 300,
                }],
            },
        }
        with mock.patch("agent.supervisor.load_config", return_value=cfg), \
             mock.patch(
                 "agent.supervisor._load_stored_rect_for_package",
                 return_value=stored,
             ), mock.patch.object(
                 window_apply, "force_resize_package",
                 return_value=(True, "verified"),
             ) as resize_mock:
            _reapply_layout_for_package("com.moons.litesc")
        resize_mock.assert_called_once()
        _args, kwargs = resize_mock.call_args
        self.assertTrue(kwargs.get("skip_windowing_mode_flip"))

    def test_do_launch_skips_layout_reapply_for_recovery(self) -> None:
        from agent.supervisor import WatchdogSupervisor
        sup = WatchdogSupervisor.__new__(WatchdogSupervisor)
        sup.cfg = {"root_mode_enabled": True}
        sup._logger = mock.MagicMock()
        sup.status_map = {}
        entry = {"package": "com.moons.litesc", "private_server_url": "https://x"}
        with mock.patch(
            "agent.supervisor.launch_package_for_current_config",
            return_value=mock.MagicMock(success=True, error=""),
        ), mock.patch(
            "agent.supervisor._reapply_layout_for_package",
        ) as layout_mock, mock.patch(
            "agent.supervisor.log_event",
        ), mock.patch(
            "agent.launch_relaunch_trace.record_launch_attempt",
        ):
            ok = WatchdogSupervisor._do_launch(sup, "com.moons.litesc", entry, "dead_recovery")
        self.assertTrue(ok)
        layout_mock.assert_not_called()

    def test_reapply_layout_skips_apply_window_layout_silent(self) -> None:
        """Recovery must not re-run global freeform setup (probe p-e2fe87273b)."""
        from agent.supervisor import _reapply_layout_for_package
        from agent.window_layout import WindowRect
        stored = WindowRect(
            package="com.moons.litesc",
            left=0, top=0, right=400, bottom=300,
        )
        cfg = {
            "screen_mode": "landscape",
            "last_layout_preview": {
                "rects": [{
                    "package": "com.moons.litesc",
                    "left": 0, "top": 0, "right": 400, "bottom": 300,
                }],
            },
        }
        with mock.patch("agent.supervisor.load_config", return_value=cfg), \
             mock.patch(
                 "agent.supervisor._load_stored_rect_for_package",
                 return_value=stored,
             ), mock.patch.object(
                 window_apply, "apply_window_layout_silent",
             ) as silent_mock, mock.patch.object(
                 window_apply, "force_resize_package",
                 return_value=(True, "verified"),
             ) as resize_mock:
            _reapply_layout_for_package("com.moons.litesc")
        silent_mock.assert_not_called()
        resize_mock.assert_called_once()


class TestFasterDeadDetectionCadence(unittest.TestCase):
    """Presence/dead detection sped up by trimming cosmetic per-package sleeps."""

    def test_per_package_pacing_is_small(self) -> None:
        from agent.supervisor import WatchdogSupervisor as W
        self.assertEqual(W.PACKAGE_CHECKING_HOLD_SECONDS, 0.5)
        self.assertEqual(W.PACKAGE_ROUND_ROBIN_TAIL_SECONDS, 0.6)
        # A full round (and thus the 2-evaluation dead confirmation) must be
        # well under the old ~3s/package so force-close is caught quickly.
        per_pkg = W.PACKAGE_CHECKING_HOLD_SECONDS + W.PACKAGE_ROUND_ROBIN_TAIL_SECONDS
        self.assertLess(per_pkg, 1.5)

    def test_render_cadence_not_slower_than_checking_hold(self) -> None:
        from agent.supervisor import WatchdogSupervisor as W
        self.assertLessEqual(
            W.DASHBOARD_RENDER_INTERVAL_SECONDS,
            W.PACKAGE_CHECKING_HOLD_SECONDS,
        )


if __name__ == "__main__":
    unittest.main()
