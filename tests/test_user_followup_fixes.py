"""Regression tests for the five user-reported follow-up issues:

1. Roblox launch setup: single 'Private Server URL' field, no mode toggle.
2. Start dashboard table is bold/readable.
3. perform_rejoin always force-stops the package before launching so
   already-open windows pick up the new bounds.
4. (Detection still uses StateTracker — covered by existing tests; new
   tests here verify the multi-phase ``Boosting/Clearing/...`` labels
   are wired into ``_colorize_status``.)
5. (Phase-state labels — see test 4.)
"""

from __future__ import annotations

import unittest
from unittest import mock


# ── 1. Simplified URL setup ──────────────────────────────────────────────────


class SimplifiedUrlSetupTest(unittest.TestCase):
    """``_setup_launch_link`` takes a single URL and writes either
    ``deeplink`` or ``web_url`` mode depending on the scheme.  No more
    multi-choice menu, no separate Launch Mode prompt."""

    def _run(self, user_input: str, current: str = "") -> dict:
        from agent import commands as _cmd
        draft = {"launch_mode": "app", "launch_url": current}
        # Patch the prompt + validator + I/O so the function runs
        # non-interactively and uses our injected URL.
        with mock.patch.object(_cmd, "_prompt", return_value=user_input), \
             mock.patch("agent.commands.validate_launch_url",
                        return_value=mock.MagicMock(warning="")):
            _cmd._setup_launch_link(draft)
        return draft

    def test_blank_clears_url_and_sets_app_mode(self) -> None:
        d = self._run("")
        self.assertEqual(d["launch_url"], "")
        self.assertEqual(d["launch_mode"], "app")

    def test_https_share_url_becomes_web_url_mode(self) -> None:
        url = "https://www.roblox.com/share?code=ABC&type=Server"
        d = self._run(url)
        self.assertEqual(d["launch_url"], url)
        self.assertEqual(d["launch_mode"], "web_url")

    def test_deep_link_becomes_deeplink_mode(self) -> None:
        url = "roblox://navigation/share_links?code=ABC&type=Server"
        d = self._run(url)
        self.assertEqual(d["launch_url"], url)
        self.assertEqual(d["launch_mode"], "deeplink")


# ── 2. Bold dashboard table ──────────────────────────────────────────────────


class BoldDashboardTableTest(unittest.TestCase):

    def test_color_constants_include_bold_attribute(self) -> None:
        """User feedback: ``i cant read shit so thin``.  Every status
        colour now starts with ``\\033[1;`` so the foreground text is
        rendered in the bold/bright weight."""
        from agent import commands as _cmd
        for name in ("_ANSI_GREEN", "_ANSI_YELLOW", "_ANSI_RED", "_ANSI_CYAN"):
            code = getattr(_cmd, name)
            self.assertTrue(
                code.startswith("\033[1;"),
                f"{name}={code!r} should start with '\\033[1;' for bold "
                f"so the cloud-phone Termux font is readable",
            )

    def test_build_start_table_emits_bold_for_plain_cells(self) -> None:
        """The Package and Username cells are not coloured but still
        need to use the bold attribute so the dashboard isn't a wall of
        hairline glyphs."""
        from agent.commands import build_start_table, _ANSI_BOLD
        rows = [(1, "com.test.client", "user", "Online")]
        out = build_start_table(rows, use_color=True)
        # The plain ``_ANSI_BOLD`` escape must appear at least once
        # outside the status colour (which has its own bold prefix).
        self.assertIn(_ANSI_BOLD, out)
        self.assertIn("com.test.client", out)
        self.assertIn("user", out)

    def test_phase_labels_are_colorized(self) -> None:
        """The new phase labels (``Boosting``, ``Clearing``, ``Layout``,
        ``Docking``, ``Waiting``, ``Resizing``) must each get a colour
        so the row visibly advances during the prep phase."""
        from agent.commands import _colorize_status
        for label in ("Boosting", "Clearing", "Layout", "Docking",
                       "Waiting", "Resizing"):
            colored = _colorize_status(label, use_color=True)
            self.assertNotEqual(
                colored, label,
                f"label {label!r} must be wrapped in ANSI codes "
                f"so the user sees progress, got {colored!r}",
            )
            self.assertIn(label, colored)


# ── 3. perform_rejoin always force-stops before launch ───────────────────────


class AlwaysForceStopBeforeLaunchTest(unittest.TestCase):
    """User feedback: ``i try to messed up already opened client
    window to see if our tool can fix it when we restart, still fail``.

    Root cause: ``perform_rejoin`` only force-stopped in root mode, so
    when Android saw an existing task it brought the window to front
    and ignored ``--activity-launch-bounds``.  The new code always
    issues ``am force-stop`` first."""

    def test_perform_rejoin_calls_force_stop_in_non_root_mode(self) -> None:
        from agent import launcher as _l
        from agent import android as _a

        fake_cfg = {
            "roblox_package": "com.moons.litesc",
            "launch_mode": "deeplink",
            "launch_url": "roblox://navigation/share_links?code=X",
            "auto_rejoin_enabled": True,
            "reconnect_delay_seconds": 5,
            "root_mode_enabled": False,   # ← key: non-root mode
            "roblox_packages": [{
                "package": "com.moons.litesc",
                "account_username": "test",
                "enabled": True,
                "username_source": "manual",
            }],
        }

        force_stop_called: list[str] = []
        def _fake_force_stop(pkg, root_info=None):
            force_stop_called.append(pkg)
            return mock.MagicMock(ok=True, summary="ok",
                                  stdout="", stderr="", args=("am",))

        launch_calls: list[tuple] = []
        def _fake_launch(pkg, rect, url):
            launch_calls.append((pkg, rect, url))
            return mock.MagicMock(ok=True, summary="", stdout="",
                                  stderr=""), "method"

        with mock.patch.object(_a, "package_installed", return_value=True), \
             mock.patch.object(_a, "force_stop_package", _fake_force_stop), \
             mock.patch.object(_a, "launch_package_with_bounds",
                               side_effect=_fake_launch), \
             mock.patch.object(_a, "detect_root",
                               return_value=mock.MagicMock(
                                   available=False, tool=None)), \
             mock.patch("agent.launcher.db") as _db, \
             mock.patch("agent.launcher.time.sleep"):
            _db.insert_rejoin_attempt = mock.MagicMock()
            _db.insert_event = mock.MagicMock()
            res = _l.perform_rejoin(fake_cfg, reason="start")

        self.assertTrue(res.success, f"launcher should report success: {res!r}")
        self.assertIn("com.moons.litesc", force_stop_called,
                      "force_stop_package must be called BEFORE launch even "
                      "in non-root mode so the existing task is destroyed "
                      "and the new launch picks up the requested bounds")
        self.assertTrue(launch_calls,
                        "launch_package_with_bounds must still run after "
                        "force-stop")

    def test_perform_rejoin_honors_no_force_stop_flag(self) -> None:
        """Reconnect-from-supervisor paths pass ``no_force_stop=True`` to
        keep their own state machine — we must not force-stop there."""
        from agent import launcher as _l
        from agent import android as _a

        fake_cfg = {
            "roblox_package": "com.moons.litesc",
            "launch_mode": "deeplink",
            "launch_url": "roblox://navigation/share_links?code=X",
            "auto_rejoin_enabled": True,
            "reconnect_delay_seconds": 5,
            "root_mode_enabled": False,
            "roblox_packages": [{
                "package": "com.moons.litesc",
                "account_username": "test",
                "enabled": True,
                "username_source": "manual",
            }],
        }

        force_stop_called: list[str] = []
        def _fake_force_stop(pkg, root_info=None):
            force_stop_called.append(pkg)
            return mock.MagicMock(ok=True, summary="ok")

        with mock.patch.object(_a, "package_installed", return_value=True), \
             mock.patch.object(_a, "force_stop_package", _fake_force_stop), \
             mock.patch.object(_a, "launch_package_with_bounds",
                               return_value=(mock.MagicMock(
                                   ok=True, summary=""), "method")), \
             mock.patch.object(_a, "detect_root",
                               return_value=mock.MagicMock(
                                   available=False, tool=None)), \
             mock.patch("agent.launcher.db") as _db, \
             mock.patch("agent.launcher.time.sleep"):
            _db.insert_rejoin_attempt = mock.MagicMock()
            _db.insert_event = mock.MagicMock()
            _l.perform_rejoin(fake_cfg, reason="disconnected",
                              no_force_stop=True)

        self.assertNotIn("com.moons.litesc", force_stop_called,
                         "no_force_stop=True must skip the pre-launch stop")


# ── 4 + 5. Phase progression helper ──────────────────────────────────────────


class PhaseAdvancementTest(unittest.TestCase):

    def test_set_all_phase_keep_failed_preserves_terminal_rows(self) -> None:
        from agent.commands import _set_all_phase_keep_failed
        phase = {
            "com.a": "Boosting",
            "com.b": "Failed",
            "com.c": "Clearing",
            "com.d": "Closed",
        }
        entries = [{"package": p} for p in phase]
        _set_all_phase_keep_failed(phase, "Resizing", entries)
        # Terminal rows preserved
        self.assertEqual(phase["com.b"], "Failed")
        self.assertEqual(phase["com.d"], "Closed")
        # In-flight rows advance
        self.assertEqual(phase["com.a"], "Resizing")
        self.assertEqual(phase["com.c"], "Resizing")


if __name__ == "__main__":
    unittest.main()
