"""Regression tests for Bug 2 (probe ``p-52aeb6420f``).

Symptom on real device (SM-N9810, Android 10):

    After all configured packages launched, the screen became
    portrait-shaped with black bars; Termux and other foreground apps
    appeared to close; the device returned to the home screen.

Root cause: ``_enforce_termux_left_layout`` was called unconditionally
during ``cmd_start`` and tried to flip the Termux task into freeform
windowing mode + resize it via ``am stack resize``.  Even when the
individual commands returned non-zero on this device (probe trace had
``set-windowing-mode rc=255``, ``cmd resize-task rc=255``, etc.), the
partial side-effects were enough to disrupt Termux's active window.

Fix: gate the dock-resize behind an opt-in config flag
``termux_dock_enabled`` (default ``False``).  The default is now to do
nothing — emit a ``[DENG_REJOIN_TERMUX_LAYOUT]`` probe event with
``success=skipped`` and return immediately.  Operators who want the old
behaviour can flip the flag.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch


class TestBug2TermuxDockOptIn(unittest.TestCase):

    def test_default_skips_termux_dock_resize(self):
        from agent.commands import _enforce_termux_left_layout

        # No flag in config — default behaviour.
        with patch("agent.termux_minimize.minimize_termux_to_dock") as mock_min:
            result = _enforce_termux_left_layout({})
        mock_min.assert_not_called()
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("skipped"))

    def test_explicit_false_skips(self):
        from agent.commands import _enforce_termux_left_layout
        with patch("agent.termux_minimize.minimize_termux_to_dock") as mock_min:
            result = _enforce_termux_left_layout({"termux_dock_enabled": False})
        mock_min.assert_not_called()
        self.assertTrue(result.get("skipped"))

    def test_opt_in_true_calls_minimize(self):
        """Operators who explicitly opt in still get the old behaviour."""
        from agent.commands import _enforce_termux_left_layout
        with patch("agent.termux_minimize.minimize_termux_to_dock") as mock_min, \
             patch("agent.termux_minimize._read_back_termux_bounds", return_value=None):
            # Configure the mock to return a result-shaped object.
            mock_result = mock_min.return_value
            mock_result.as_dict.return_value = {
                "ok": False, "skipped": True, "reason": "test stub",
            }
            mock_result.display = (1280, 720)
            mock_result.desired = (0, 0, 640, 720)
            mock_result.actual = None
            mock_result.ok = False
            mock_result.method = "stub"
            mock_result.reason = "test stub"

            _enforce_termux_left_layout({"termux_dock_enabled": True})
            mock_min.assert_called_once()

    def test_skipped_default_emits_termux_layout_probe_tag(self):
        """The skip path must still emit the [DENG_REJOIN_TERMUX_LAYOUT] tag."""
        from agent.commands import _enforce_termux_left_layout

        captured: list[str] = []

        def _fake_log_event(_logger, _level, tag, **kw):
            captured.append(tag)

        with patch("agent.logger.log_event", side_effect=_fake_log_event):
            _enforce_termux_left_layout({})

        self.assertIn("[DENG_REJOIN_TERMUX_LAYOUT]", captured)

    def test_default_config_does_not_opt_in(self):
        """``default_config()`` MUST NOT set ``termux_dock_enabled=True``."""
        from agent.config import default_config
        cfg = default_config()
        # Either the key is absent OR it is explicitly False.
        self.assertFalse(
            bool(cfg.get("termux_dock_enabled", False)),
            "Bug 2 regression: termux_dock_enabled must default to False.",
        )

    def test_skip_path_never_touches_termux_package(self):
        """When skipped, no agent.android force-stop or resize call is made."""
        from agent.commands import _enforce_termux_left_layout
        with patch("agent.android.force_stop_package") as mock_stop, \
             patch("agent.android.run_root_command") as mock_root, \
             patch("agent.termux_minimize.minimize_termux_to_dock") as mock_min:
            _enforce_termux_left_layout({})
        mock_stop.assert_not_called()
        mock_root.assert_not_called()
        mock_min.assert_not_called()

    def test_cmd_start_defaults_termux_dock_disabled(self):
        """Normal Start must not opt in to Termux docking when config omits the flag."""
        import inspect
        import agent.commands as commands

        src = inspect.getsource(commands.cmd_start)
        self.assertIn('cfg.get("termux_dock_enabled", False)', src)
        self.assertNotIn('cfg.get("termux_dock_enabled", True)', src)

    def test_cmd_start_does_not_run_global_kill_all(self):
        """Normal Start must not close all/cached apps as a layout workaround."""
        import inspect
        import agent.commands as commands

        src = inspect.getsource(commands.cmd_start)
        self.assertNotIn("kill_all_background_apps(", src)
        self.assertIn("[DENG_REJOIN_START_SAFETY]", src)


if __name__ == "__main__":
    unittest.main()
