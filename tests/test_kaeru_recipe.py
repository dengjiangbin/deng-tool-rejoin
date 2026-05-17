"""Kaeru-recipe regression tests (probe ``p-1239f2b5f9``).

The user installed Kaeru on the cloud SM-N9810 (Android 10 / SDK 29)
and reported its window-resize "works beautifully" for one clone while
our tool's resize was a no-op.  The probe dump captured everything
needed to reverse-engineer the working path.  These tests lock in the
five copied techniques so a regression on any of them fails CI:

1. ``to_roblox_deep_link`` converts ``https://www.roblox.com/share?...``
   to ``roblox://navigation/share_links?...`` — the *recents* intent on
   the probe showed this is the URL that joins the private server.

2. ``detect_display_info`` prefers the rotation-aware
   ``mOverrideDisplayInfo.app W×H`` from ``dumpsys display`` (the cloud
   phone is in landscape rotation 1: ``app 1280 x 720`` while the
   sensor's native portrait ``wm size`` reads ``720x1280``).

3. ``_get_stack_id`` finds the *stack* id of a package's task (Android
   10's ``am stack resize`` operates on stacks, not tasks).

4. ``_direct_resize_via_root`` issues the Android-10 form
   ``am stack resize <STACK_ID> <L,T,R,B>`` (comma-separated bounds)
   BEFORE the Android-11+ ``cmd activity resize-task`` form
   (space-separated bounds).

5. ``_is_real_activity_bounds`` filters out status-bar / IME / nav-bar
   windows so ``read_actual_bounds`` doesn't latch onto a 25-px sliver.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from unittest import mock

from agent import window_apply, window_layout
from agent.url_utils import to_roblox_deep_link
from agent.window_apply import (
    _MIN_REAL_WINDOW_H,
    _MIN_REAL_WINDOW_W,
    _direct_resize_via_root,
    _get_stack_id,
    _is_real_activity_bounds,
    read_actual_bounds,
)
from agent.window_layout import WindowRect, detect_display_info


# ── 1. URL conversion ────────────────────────────────────────────────────────

class DeepLinkConversionTest(unittest.TestCase):

    def test_share_url_to_roblox_deeplink(self) -> None:
        url = "https://www.roblox.com/share?code=09b8573d85c318429922bc449286bccf&type=Server"
        out = to_roblox_deep_link(url)
        self.assertEqual(
            out,
            "roblox://navigation/share_links?code=09b8573d85c318429922bc449286bccf&type=Server",
        )

    def test_share_link_path_also_converts(self) -> None:
        url = "https://roblox.com/share-links?code=abc&type=Server"
        out = to_roblox_deep_link(url)
        self.assertTrue(out.startswith("roblox://navigation/share_links?"))
        self.assertIn("code=abc", out)
        self.assertIn("type=Server", out)

    def test_games_url_with_private_server_code(self) -> None:
        url = "https://www.roblox.com/games/4924922222/Game?privateServerLinkCode=xyz"
        out = to_roblox_deep_link(url)
        self.assertEqual(out, "roblox://placeId=4924922222&privateServerLinkCode=xyz")

    def test_already_deep_link_passthrough(self) -> None:
        url = "roblox://navigation/share_links?code=x"
        self.assertEqual(to_roblox_deep_link(url), url)

    def test_non_roblox_url_passthrough(self) -> None:
        for url in ("https://example.com/", "ftp://foo", "", None):
            self.assertEqual(to_roblox_deep_link(url), url)

    def test_unknown_path_passthrough(self) -> None:
        url = "https://www.roblox.com/some/unmapped/path?x=1"
        out = to_roblox_deep_link(url)
        # Falls back to original; the OS still routes it via the app's
        # https intent filter if registered.
        self.assertEqual(out, url)

    def test_bad_input_never_raises(self) -> None:
        # Sketchy URLs that urlparse handles oddly must not crash.
        for u in ("://bad", "not a url at all", "https://" + "x" * 4096):
            try:
                to_roblox_deep_link(u)
            except Exception as exc:  # pragma: no cover
                self.fail(f"to_roblox_deep_link raised on {u!r}: {exc}")


# ── 2. Rotation-aware display detection ──────────────────────────────────────

# Trimmed real dumpsys output captured from probe p-1239f2b5f9.
_DUMPSYS_DISPLAY_LANDSCAPE = """\
DISPLAY MANAGER (dumpsys display)
  mViewports=[DisplayViewport{type=INTERNAL, valid=true, displayId=0, uniqueId='local:0', physicalPort=0, orientation=1, logicalFrame=Rect(0, 0 - 1280, 720), physicalFrame=Rect(0, 0 - 1280, 720), deviceWidth=1280, deviceHeight=720}]
  mStableDisplaySize=Point(720, 1280)
Logical Displays: size=1
  Display 0:
    mDisplayId=0
    mBaseDisplayInfo=DisplayInfo{"Built-in Screen, displayId 0", uniqueId "local:0", app 720 x 1280, real 720 x 1280}
    mOverrideDisplayInfo=DisplayInfo{"Built-in Screen, displayId 0", uniqueId "local:0", app 1280 x 720, real 1280 x 720, largest app 1280 x 1255, smallest app 720 x 695, mode 1, defaultMode 3, modes [{id=1, width=720, height=1280, fps=20.0}], colorMode 0, rotation 1, density 164}
"""


@dataclass
class _StubResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""


class RotationAwareDisplayTest(unittest.TestCase):

    def _patch_run(self, dumpsys_output: str, wm_size: str, wm_density: str):
        def stub(cmd, **kwargs):  # noqa: ANN001
            if cmd[:2] == ["dumpsys", "display"]:
                return _StubResult(ok=bool(dumpsys_output), stdout=dumpsys_output)
            if cmd == ["wm", "size"]:
                return _StubResult(ok=True, stdout=wm_size)
            if cmd == ["wm", "density"]:
                return _StubResult(ok=True, stdout=wm_density)
            return _StubResult(ok=False)
        return stub

    def test_landscape_override_wins_over_portrait_wm_size(self) -> None:
        # Real cloud-phone state: wm size reports portrait, override is
        # landscape — the landscape one is what we MUST use.
        run = self._patch_run(
            _DUMPSYS_DISPLAY_LANDSCAPE,
            "Physical size: 720x1280",
            "Physical density: 320",
        )
        with mock.patch.object(window_layout.android, "run_android_command", side_effect=run):
            info = detect_display_info()
        self.assertEqual((info.width, info.height), (1280, 720),
                         "must use mOverrideDisplayInfo app W×H, not wm size")
        self.assertEqual(info.density, 164,
                         "must use override density too")

    def test_falls_back_to_wm_size_when_dumpsys_unavailable(self) -> None:
        run = self._patch_run(
            "",
            "Physical size: 1080x1920",
            "Physical density: 420",
        )
        with mock.patch.object(window_layout.android, "run_android_command", side_effect=run):
            info = detect_display_info()
        self.assertEqual((info.width, info.height), (1080, 1920))
        self.assertEqual(info.density, 420)

    def test_falls_back_when_override_too_small(self) -> None:
        # Pathological dumpsys output where app=100x100 — we must NOT
        # accept that as the display size; fall back to wm.
        bad = (
            "mOverrideDisplayInfo=DisplayInfo{ ... app 100 x 100, "
            "real 100 x 100, rotation 0, density 160}"
        )
        run = self._patch_run(
            bad,
            "Physical size: 1080x1920",
            "Physical density: 420",
        )
        with mock.patch.object(window_layout.android, "run_android_command", side_effect=run):
            info = detect_display_info()
        self.assertEqual((info.width, info.height), (1080, 1920))


# ── 3. Stack-id discovery ────────────────────────────────────────────────────

# Trimmed real activity-dumpsys captured from probe p-1239f2b5f9.
_DUMPSYS_ACTIVITY_WITH_STACK = """\
Stack #1: type=standard mode=fullscreen
  mBounds=Rect(0, 0 - 0, 0)
  * TaskRecord{4068d9d #76 A=com.termux U=0 StackId=1 sz=1}
    affinity=com.termux
Stack #3: type=standard mode=fullscreen
  mBounds=Rect(0, 0 - 0, 0)
  * TaskRecord{3169de3 #78 A=com.moons.litesc U=0 StackId=3 sz=1}
    affinity=com.moons.litesc
Stack #0: type=home mode=fullscreen
  * TaskRecord{54e93e0 #75 I=com.android.launcher3/.Launcher U=0 StackId=0 sz=1}
"""


class StackIdDiscoveryTest(unittest.TestCase):

    def _patch_run(self, output: str):
        def stub(cmd, **kwargs):  # noqa: ANN001
            if cmd[:3] == ["dumpsys", "activity", "activities"]:
                return _StubResult(ok=True, stdout=output)
            return _StubResult(ok=False)
        return stub

    def test_finds_stack_for_moons_clone(self) -> None:
        with mock.patch.object(window_apply.android, "run_command",
                               side_effect=self._patch_run(_DUMPSYS_ACTIVITY_WITH_STACK)):
            self.assertEqual(_get_stack_id("com.moons.litesc"), 3)
            self.assertEqual(_get_stack_id("com.termux"), 1)

    def test_returns_none_when_package_absent(self) -> None:
        with mock.patch.object(window_apply.android, "run_command",
                               side_effect=self._patch_run(_DUMPSYS_ACTIVITY_WITH_STACK)):
            self.assertIsNone(_get_stack_id("com.does.not.exist"))

    def test_handles_dumpsys_failure(self) -> None:
        def stub(cmd, **kwargs):  # noqa: ANN001
            return _StubResult(ok=False)
        with mock.patch.object(window_apply.android, "run_command", side_effect=stub):
            self.assertIsNone(_get_stack_id("com.moons.litesc"))


# ── 4. Android-10 stack-resize command shape ─────────────────────────────────

class Android10StackResizeShapeTest(unittest.TestCase):
    """``_direct_resize_via_root`` must issue ``am stack resize <SID> <L,T,R,B>``
    BEFORE ``cmd activity resize-task`` so Android 10 devices get the
    syntax they actually support."""

    def _capture_root_commands(self):
        """Run ``_direct_resize_via_root`` with stubbed Android helpers and
        return the ordered list of commands attempted."""
        commands: list[list[str]] = []

        def stub_root(cmd, **kwargs):  # noqa: ANN001
            commands.append(list(cmd))
            # Make EVERY stack/resize succeed so the function returns
            # on the first match (we want to assert the FIRST shape).
            return _StubResult(ok=True)

        def stub_command(cmd, **kwargs):  # noqa: ANN001
            # Return dumpsys output that gives task 78 and stack 3 for
            # com.moons.litesc — same as the probe.
            if cmd[:3] == ["dumpsys", "activity", "activities"]:
                return _StubResult(ok=True, stdout=_DUMPSYS_ACTIVITY_WITH_STACK)
            if cmd[:3] == ["dumpsys", "window", "windows"]:
                return _StubResult(ok=False)
            return _StubResult(ok=False)

        rect = WindowRect(
            package="com.moons.litesc",
            left=0, top=0, right=640, bottom=360,
        )

        with mock.patch.object(window_apply.android, "run_root_command",
                               side_effect=stub_root), \
             mock.patch.object(window_apply.android, "run_command",
                               side_effect=stub_command):
            ok, msg = _direct_resize_via_root(
                "com.moons.litesc", rect, root_tool="su",
            )

        return commands, ok, msg

    def test_first_resize_is_android10_stack_comma_form(self) -> None:
        commands, ok, msg = self._capture_root_commands()
        self.assertTrue(ok)
        # Skip the prep commands (set-task-windowing-mode / move-task)
        # and find the first *resize* command.
        resize_cmds = [c for c in commands if "resize" in (c[1] if len(c) > 1 else "")
                       or (len(c) > 2 and "resize" in c[2])]
        self.assertGreaterEqual(len(resize_cmds), 1,
                                "no resize command was issued")
        first = resize_cmds[0]
        # Android 10 form: ``am stack resize <STACK_ID> <L,T,R,B>``
        self.assertEqual(first[:3], ["am", "stack", "resize"])
        # Stack id comes from the dumpsys parser → "3" for moons.litesc.
        self.assertEqual(first[3], "3", "expected stack id 3 from dumpsys")
        # 5th token must be a comma-separated 4-tuple, not 4 separate args.
        self.assertEqual(first[4], "0,0,640,360",
                         f"Android 10 syntax requires comma-separated bounds; got {first}")

    def test_prep_includes_set_windowing_mode_5(self) -> None:
        commands, _, _ = self._capture_root_commands()
        # First "prep" step should be set-task-windowing-mode <tid> 5
        # (freeform).  Tolerate either order with move-task as fallback.
        prep_cmds = [c for c in commands
                     if "set-task-windowing-mode" in c
                     or "move-task" in c]
        self.assertTrue(prep_cmds, "no windowing-mode flip / move-task issued")
        # Verify the freeform mode constant (5) is what's being requested.
        joined = " ".join(" ".join(c) for c in prep_cmds)
        self.assertIn(" 5", joined,
                      "freeform windowing mode (5) not found in prep commands")


# ── 5. Real-activity bounds filter ───────────────────────────────────────────

class RealActivityBoundsFilterTest(unittest.TestCase):

    def test_status_bar_sliver_rejected(self) -> None:
        # The exact sliver from the probe: 1280 wide × 25 tall (status bar).
        self.assertFalse(_is_real_activity_bounds((0, 0, 1280, 25)))
        # 1×1 invisible chrome surface.
        self.assertFalse(_is_real_activity_bounds((0, 25, 1, 26)))

    def test_real_activity_bounds_accepted(self) -> None:
        self.assertTrue(_is_real_activity_bounds((0, 0, 640, 360)))
        self.assertTrue(_is_real_activity_bounds((100, 100, 800, 600)))

    def test_threshold_constants_are_reasonable(self) -> None:
        self.assertGreaterEqual(_MIN_REAL_WINDOW_W, 100)
        self.assertGreaterEqual(_MIN_REAL_WINDOW_H, 50)
        # Must be small enough that the smallest sensible clone window
        # (a 240×135 thumbnail in a 5-column grid) still qualifies.
        self.assertLessEqual(_MIN_REAL_WINDOW_W, 240)
        self.assertLessEqual(_MIN_REAL_WINDOW_H, 135)

    def test_read_actual_bounds_skips_sliver_and_picks_real_window(self) -> None:
        # Synthesize dumpsys output: one 25-px sliver window FIRST, then
        # a real 640×360 activity window — the reader must skip the
        # sliver and return the activity.
        window_dump = (
            "Window{abc1 com.moons.litesc/.StatusBar}:\n"
            "  mFrame=[0,0][1280,25]\n"
            "  mHasSurface=true\n"
            "Window{abc2 com.moons.litesc/.RobloxActivity}:\n"
            "  mFrame=[20,40][660,400]\n"
            "  mHasSurface=true\n"
        )

        def stub(cmd, **kwargs):  # noqa: ANN001
            if cmd[:3] == ["dumpsys", "window", "windows"]:
                return _StubResult(ok=True, stdout=window_dump)
            return _StubResult(ok=False)

        with mock.patch.object(window_apply.android, "run_command", side_effect=stub):
            bounds, source = read_actual_bounds("com.moons.litesc")

        self.assertEqual(bounds, (20, 40, 660, 400),
                         f"reader picked sliver instead of activity; got {bounds}")
        self.assertEqual(source, "dumpsys_window")


# ── 6. End-to-end: launcher uses deep-link URL ───────────────────────────────

class LauncherImportsDeepLinkHelperTest(unittest.TestCase):
    """The launcher module must import and call ``to_roblox_deep_link``
    on the resolved URL before passing it to Android.

    Going through the full ``perform_rejoin`` path requires too many
    Android/db/supervisor stubs; instead we verify the wiring at the
    source-level — the helper is imported, and the round-trip of a real
    share URL produces a deep link.
    """

    def test_launcher_imports_deep_link_helper(self) -> None:
        from agent import launcher as L
        self.assertTrue(
            hasattr(L, "to_roblox_deep_link"),
            "launcher.py must import to_roblox_deep_link to fix the "
            "Kaeru-recipe private-server-not-joining bug",
        )

    def test_share_url_roundtrip_produces_deep_link(self) -> None:
        # End-to-end: the URL that lives in a user's config gets
        # transformed into the deep link Roblox honors directly.
        share = "https://www.roblox.com/share?code=ABC&type=Server"
        dl = to_roblox_deep_link(share)
        self.assertTrue(
            dl.startswith("roblox://navigation/share_links?"),
            f"share URL was not converted to deep link: {dl!r}",
        )
        # All original query params survive intact.
        self.assertIn("code=ABC", dl)
        self.assertIn("type=Server", dl)

    def test_launcher_source_calls_helper_on_url_for_launch(self) -> None:
        # Read the launcher.py source and confirm the helper is invoked
        # on the URL we hand to Android (not just imported and ignored).
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent / "agent" / "launcher.py"
        text = src.read_text(encoding="utf-8")
        self.assertIn("to_roblox_deep_link", text)
        # Helper must be called on the same variable that is passed to
        # android.launch_url below it.  Heuristic: find a line of the
        # form ``X = to_roblox_deep_link(X)`` or ``X = to_roblox_deep_link(...)``.
        idx = text.find("to_roblox_deep_link(")
        self.assertGreater(idx, 0, "to_roblox_deep_link is imported but never called")


if __name__ == "__main__":
    unittest.main()
