"""Regression tests for probe ``p-47fa33562a`` fixes.

Four bugs the user hit on the cloud phone, each gets a focused test:

1. Per-clone launch passed ``[package]`` (one) to
   ``calculate_split_layout`` so every clone got the *same* rect and
   they all overlapped on screen.  Now the launcher passes the full
   enabled-package list and picks ``rect.package == package``.

2. Termux dock minimizer reported ``ok=True`` after ``am stack resize``
   returned rc=0 on Android 13+ — but the command is deprecated and
   was a no-op.  Read-back is now required for trusted success; a
   best-effort win without read-back must NOT claim ``ok=True``.

3. ``cmd_start`` and ``main`` Ctrl-C paths must skip Python finaliz-
   ation on Termux (``os._exit``) to dodge the libc-shutdown SIGSEGV
   the user saw on every clean stop.

4. ``am start -a VIEW -d <url> <pkg>`` was bringing the existing
   Roblox lobby to the foreground instead of consuming the private-
   server share URL.  ``launch_url`` / ``launch_url_generic`` now
   pass ``-f 0x14208000`` (CLEAR_TASK | CLEAR_TOP | NEW_TASK |
   RESET_TASK_IF_NEEDED) so the URL is always re-routed.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock


class LauncherPicksOwnRectFromFullListTest(unittest.TestCase):
    """``perform_rejoin`` must build the layout for ALL enabled packages."""

    def _stub_modules(self):  # noqa: ANN001
        """Patch the deps used inside ``perform_rejoin`` so we can drive
        it from a unit test without hitting Android."""
        captured: dict = {}

        # ``android.package_installed`` -> True; ``detect_root`` -> no root
        # so we skip the force-stop branch.
        mod_patches = [
            mock.patch("agent.android.package_installed", return_value=True),
            mock.patch("agent.android.detect_root",
                       return_value=mock.Mock(available=False, tool=None)),
        ]

        # ``window_layout.detect_display_info`` returns a 720×1280 device.
        from agent import window_layout
        mod_patches.append(mock.patch.object(
            window_layout, "detect_display_info",
            return_value=window_layout.DisplayInfo(
                width=720, height=1280, density=320,
            ),
        ))

        # Capture what the launcher asks the layout for.
        original_split = window_layout.calculate_split_layout
        def _record_split(*args, **kwargs):  # noqa: ANN001
            captured["split_args"] = (args, kwargs)
            return original_split(*args, **kwargs)
        mod_patches.append(mock.patch.object(
            window_layout, "calculate_split_layout",
            side_effect=_record_split,
        ))

        # Capture what bounds the launcher hands the OS.
        from agent import android
        def _fake_launch_with_bounds(pkg, rect, url):  # noqa: ANN001
            captured["bounds_call"] = {
                "package": pkg, "rect": rect, "url": url,
            }
            return mock.Mock(ok=True, stderr="", stdout=""), "bounds_freeform"
        mod_patches.append(mock.patch.object(
            android, "launch_package_with_bounds",
            side_effect=_fake_launch_with_bounds,
        ))

        # ``launch_package_with_options`` is the fallback path; if we ever
        # take it the test fails (we expect bounds to be set).
        def _fake_options(*a, **k):  # noqa: ANN001, ANN002
            captured["options_called"] = True
            return mock.Mock(ok=True, stderr="", stdout=""), "options_fallback"
        mod_patches.append(mock.patch.object(
            android, "launch_package_with_options",
            side_effect=_fake_options,
        ))

        # Quiet the local SQLite recorder.
        from agent import db
        mod_patches.append(mock.patch.object(
            db, "insert_rejoin_attempt",
        ))
        mod_patches.append(mock.patch.object(db, "insert_event"))

        return captured, mod_patches

    def test_full_package_list_passed_to_layout(self) -> None:
        """Layout MUST be calculated for all three enabled clones,
        not just the one we're currently launching."""
        from agent import launcher  # noqa: PLC0415

        captured, patches = self._stub_modules()
        cfg = {
            "roblox_package": "com.moons.litesc",
            "launch_mode": "web_url",
            "launch_url": "https://www.roblox.com/share?code=PLACEHOLDER&type=Server",
            "log_level": "INFO",
            "reconnect_delay_seconds": 0,
            "packages": [
                {"package": "com.moons.litesc",
                 "private_server_url": "https://www.roblox.com/share?code=X&type=Server",
                 "enabled": True},
                {"package": "com.moons.litesd",
                 "private_server_url": "https://www.roblox.com/share?code=Y&type=Server",
                 "enabled": True},
                {"package": "com.moons.litese",
                 "private_server_url": "https://www.roblox.com/share?code=Z&type=Server",
                 "enabled": True},
            ],
            "termux_dock_fraction": 0.50,
        }
        entry = cfg["packages"][1]  # launching the MIDDLE clone (litesd)

        # ``validate_config`` is strict about other fields; bypass it for
        # this focused unit test.  We only care about the launcher's
        # behaviour AFTER config validation.
        from agent import launcher as _launcher_mod
        def _passthrough(c):  # noqa: ANN001
            return c
        with patches[0], patches[1], patches[2], patches[3], \
             patches[4], patches[5], patches[6], patches[7], \
             mock.patch.object(_launcher_mod, "validate_config",
                               side_effect=_passthrough), \
             mock.patch.object(_launcher_mod, "configure_logging",
                               return_value=mock.Mock()), \
             mock.patch.object(_launcher_mod, "enabled_package_entries",
                               return_value=cfg["packages"]), \
             mock.patch.object(_launcher_mod, "effective_private_server_url",
                               return_value=entry["private_server_url"]):
            launcher.perform_rejoin(
                cfg, reason="manual", package_entry=entry, no_force_stop=True,
            )

        self.assertIn("split_args", captured,
                      "calculate_split_layout MUST be called for bounds launch")
        pkgs, _kwargs = captured["split_args"]
        called_pkgs = list(pkgs[0])
        # Bug regression: we passed only [package] in the old code.
        self.assertEqual(
            len(called_pkgs), 3,
            f"layout must see all 3 clones, got {called_pkgs}",
        )
        self.assertEqual(
            set(called_pkgs),
            {"com.moons.litesc", "com.moons.litesd", "com.moons.litese"},
            "every enabled clone must be in the layout calc",
        )

        # And the launcher must pick THIS package's rect, not the first
        # one.  litesd is the middle clone; its rect must differ from
        # litesc's first-row rect.
        bounds_call = captured.get("bounds_call")
        self.assertIsNotNone(bounds_call, "launch_with_bounds was not called")
        self.assertEqual(bounds_call["package"], "com.moons.litesd")

        # The middle clone's "top" must be greater than the first clone's
        # top — i.e. it's the SECOND row of the layout, not row 1.
        from agent import window_layout
        all_rects = window_layout.calculate_split_layout(
            ["com.moons.litesc", "com.moons.litesd", "com.moons.litese"],
            720, 1280, termux_log_fraction=0.50,
        )
        litesd_rect = next(r for r in all_rects if r.package == "com.moons.litesd")
        self.assertEqual(
            bounds_call["rect"],
            (litesd_rect.left, litesd_rect.top,
             litesd_rect.right, litesd_rect.bottom),
            "launcher must pass litesd's OWN rect, not row-1's rect",
        )


class TermuxMinimizeNoFalsePositiveTest(unittest.TestCase):
    """``am stack resize`` is deprecated → its rc=0 must NOT claim success."""

    def _stub_android(self, attempt_outcomes: list[tuple[str, int]]):
        """Stub ``android.run_root_command`` to replay scripted outcomes.

        ``attempt_outcomes`` is a list of (command_keyword, rc) pairs.
        The first matching keyword in the executed command wins.
        """
        from agent import android
        def _fake(args, **kwargs):  # noqa: ANN001, ANN002
            cmd = " ".join(args)
            for needle, rc in attempt_outcomes:
                if needle in cmd:
                    return mock.Mock(
                        ok=(rc == 0), returncode=rc, stdout="", stderr="",
                    )
            return mock.Mock(ok=False, returncode=255, stdout="", stderr="")
        return mock.patch.object(android, "run_root_command", side_effect=_fake)

    def _fake_root_info(self):
        """Stub ``android.detect_root`` returning su available."""
        from agent import android
        return mock.patch.object(
            android, "detect_root",
            return_value=mock.Mock(available=True, tool="su", detail="uid=0"),
        )

    def _fake_display(self, w=720, h=1280):
        from agent import termux_minimize as tm
        return mock.patch.object(
            tm, "detect_display_info",
            return_value=mock.Mock(width=w, height=h, density=320),
        )

    def _fake_task_lookup(self, tid=42):
        from agent import termux_minimize as tm
        return mock.patch.object(
            tm, "_find_termux_task_id",
            return_value=(tid, "dumpsys activity activities"),
        )

    def test_am_stack_rc0_without_readback_is_not_ok(self) -> None:
        """Exact bug from probe ``p-47fa33562a``: every trusted command
        fails, only ``am stack resize`` returns rc=0, and dumpsys read-
        back is unavailable.  Result MUST NOT be ``ok=True``."""
        from agent import termux_minimize as tm

        # All trusted variants fail (rc=255); only am stack returns rc=0.
        outcomes = [
            ("set-task-windowing-mode", 255),
            ("cmd activity resize-task", 255),
            ("am task resize",  255),
            ("wm task resize",  255),
            ("am stack resize", 0),     # the lying deprecated command
        ]
        with self._fake_display(), self._fake_task_lookup(tid=24), \
             self._fake_root_info(), \
             self._stub_android(outcomes), \
             mock.patch.object(tm, "_read_back_termux_bounds", return_value=None):
            res = tm.minimize_termux_to_dock(fraction=0.50)
        self.assertFalse(
            res.ok,
            f"am stack resize alone must NOT claim ok=True; got {res.as_dict()}",
        )
        self.assertIn("unverified", res.method,
                      "method label must signal unverified state")
        self.assertIn("readback unavailable", res.reason)

    def test_trusted_cmd_rc0_without_readback_is_ok(self) -> None:
        """``cmd activity resize-task`` is trusted — rc=0 means success
        even when read-back fails."""
        from agent import termux_minimize as tm

        outcomes = [
            ("set-task-windowing-mode", 0),
            ("cmd activity resize-task", 0),  # trusted; wins
        ]
        with self._fake_display(), self._fake_task_lookup(tid=24), \
             self._fake_root_info(), \
             self._stub_android(outcomes), \
             mock.patch.object(tm, "_read_back_termux_bounds", return_value=None):
            res = tm.minimize_termux_to_dock(fraction=0.50)
        self.assertTrue(res.ok,
                        f"trusted rc=0 must claim ok=True; got {res.as_dict()}")
        self.assertNotIn("unverified", res.method)

    def test_default_dock_fraction_is_50_percent(self) -> None:
        """User explicitly asked for a 50% Termux dock — the default in
        ``cmd_start`` should produce a 50% wide rect on 720×1280."""
        from agent import termux_minimize as tm

        rect = tm._dock_rect(
            mock.Mock(width=720, height=1280, density=320),
            fraction=0.50,
        )
        # left=0, top=0, right=360 (50% of 720), bottom=1280
        self.assertEqual(rect[0], 0)
        self.assertEqual(rect[1], 0)
        self.assertEqual(rect[2], 360, f"expected ~50% width=360, got {rect}")
        self.assertEqual(rect[3], 1280)


class TermuxExitCleanTest(unittest.TestCase):
    """``_termux_exit_clean`` MUST call ``os._exit`` on Termux only."""

    def test_no_exit_when_not_termux(self) -> None:
        from agent import commands  # noqa: PLC0415
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TERMUX_VERSION", None)
            # Must NOT call os._exit when not on Termux.
            with mock.patch("os._exit") as ex:
                commands._termux_exit_clean()
            ex.assert_not_called()

    def test_exit_when_termux(self) -> None:
        from agent import commands  # noqa: PLC0415
        env = dict(os.environ)
        env["TERMUX_VERSION"] = "0.118"
        env.pop("DENG_DISABLE_TERMUX_HARD_EXIT", None)
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("os._exit") as ex:
            commands._termux_exit_clean()
        ex.assert_called_once_with(0)

    def test_escape_hatch_disables_exit(self) -> None:
        """``DENG_DISABLE_TERMUX_HARD_EXIT=1`` lets devs keep Python
        finalization for debugging."""
        from agent import commands  # noqa: PLC0415
        env = dict(os.environ)
        env["TERMUX_VERSION"] = "0.118"
        env["DENG_DISABLE_TERMUX_HARD_EXIT"] = "1"
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("os._exit") as ex:
            commands._termux_exit_clean()
        ex.assert_not_called()


class LaunchUrlClearTaskFlagsTest(unittest.TestCase):
    """``am start -a VIEW -d <url>`` must include CLEAR_TASK flags so the
    private-server URL is consumed instead of being routed to the
    foregrounded Roblox lobby."""

    def test_launch_url_uses_clear_task_flag(self) -> None:
        from agent import android  # noqa: PLC0415

        seen_cmds: list[list[str]] = []
        def fake_run(cmd, **kw):  # noqa: ANN001, ANN002
            seen_cmds.append(list(cmd))
            return mock.Mock(ok=True, returncode=0, stdout="", stderr="")
        with mock.patch.object(android, "run_command", side_effect=fake_run):
            android.launch_url(
                "com.moons.litesc",
                "https://www.roblox.com/share?code=ABC&type=Server",
                "web_url",
            )
        # First successful call should carry -f <flags> with CLEAR_TASK
        # bit set (bit 15 = 0x00008000).
        joined = " ".join(seen_cmds[0])
        self.assertIn("-f", joined,
                      f"missing -f intent flag: {seen_cmds[0]}")
        # Pull the hex value after -f and confirm CLEAR_TASK is set.
        idx = seen_cmds[0].index("-f")
        flags_hex = seen_cmds[0][idx + 1]
        flags = int(flags_hex, 16)
        self.assertTrue(flags & 0x00008000,
                        f"FLAG_ACTIVITY_CLEAR_TASK not set in 0x{flags:08x}")
        self.assertTrue(flags & 0x10000000,
                        f"FLAG_ACTIVITY_NEW_TASK not set in 0x{flags:08x}")

    def test_launch_url_generic_uses_clear_task_flag(self) -> None:
        from agent import android  # noqa: PLC0415

        seen_cmds: list[list[str]] = []
        def fake_run(cmd, **kw):  # noqa: ANN001, ANN002
            seen_cmds.append(list(cmd))
            return mock.Mock(ok=True, returncode=0, stdout="", stderr="")
        with mock.patch.object(android, "run_command", side_effect=fake_run):
            android.launch_url_generic(
                "https://www.roblox.com/share?code=ABC&type=Server",
                "web_url",
            )
        joined = " ".join(seen_cmds[0])
        self.assertIn("-f", joined,
                      f"missing -f intent flag: {seen_cmds[0]}")
        idx = seen_cmds[0].index("-f")
        flags = int(seen_cmds[0][idx + 1], 16)
        self.assertTrue(flags & 0x00008000,
                        f"CLEAR_TASK not set in 0x{flags:08x}")


class DockFractionPropagatesToLayoutTest(unittest.TestCase):
    """``calculate_split_layout`` must honor cfg.termux_dock_fraction so
    the clones don't overlap the Termux pane."""

    def test_50_percent_termux_means_50_percent_clones(self) -> None:
        from agent import window_layout  # noqa: PLC0415

        rects = window_layout.calculate_split_layout(
            ["com.moons.litesc", "com.moons.litesd", "com.moons.litese"],
            720, 1280,
            termux_log_fraction=0.50,
        )
        self.assertEqual(len(rects), 3)
        for r in rects:
            self.assertGreaterEqual(
                r.left, 360,
                f"clone {r.package} left={r.left} overlaps Termux pane "
                f"(should be >= 50% of 720 = 360)",
            )

    def test_clones_do_not_overlap_each_other(self) -> None:
        from agent import window_layout  # noqa: PLC0415

        rects = window_layout.calculate_split_layout(
            ["com.moons.litesc", "com.moons.litesd", "com.moons.litese"],
            720, 1280,
            termux_log_fraction=0.50,
        )
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                a, b = rects[i], rects[j]
                overlap = not (a.right <= b.left or b.right <= a.left
                               or a.bottom <= b.top or b.bottom <= a.top)
                self.assertFalse(
                    overlap,
                    f"{a.package} and {b.package} overlap "
                    f"({a.left},{a.top},{a.right},{a.bottom}) vs "
                    f"({b.left},{b.top},{b.right},{b.bottom})",
                )


if __name__ == "__main__":
    unittest.main()
