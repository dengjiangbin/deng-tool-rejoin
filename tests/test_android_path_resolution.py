"""Path-resolution for Android system binaries on Termux.

Termux's default ``$PATH`` does NOT include ``/system/bin`` — so every
direct call to ``dumpsys`` / ``wm`` / ``cmd`` / ``settings`` / ``pm`` /
``am`` etc. fails with ENOENT silently.  This regression was caught by
probe ``p-368a65d699`` from a real Samsung SM-N9810 cloud phone:

    "wm size: rc=127 [Errno 2] No such file or directory: 'wm'"
    "dumpsys display: rc=127 [Errno 2] No such file or directory: 'dumpsys'"

These tests pin the auto-resolution behaviour so the regression cannot
return.  They mock ``os.path.isfile`` / ``os.access`` to simulate the
presence of ``/system/bin/<name>`` without depending on the real
filesystem.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import android  # noqa: E402


def _exists(path: str) -> bool:
    """Pretend ``/system/bin/<known-binary>`` always exists."""
    return path.startswith("/system/bin/")


def _access(path: str, mode: int) -> bool:  # noqa: ARG001
    return path.startswith("/system/bin/")


class ResolveAndroidBinaryTests(unittest.TestCase):
    def test_resolves_known_binaries_to_system_bin(self) -> None:
        with patch.object(os.path, "isfile", side_effect=_exists), \
             patch.object(os, "access", side_effect=_access):
            for name in ("dumpsys", "wm", "cmd", "settings", "am", "pm", "pidof", "pgrep"):
                with self.subTest(name=name):
                    self.assertEqual(
                        android._resolve_android_binary(name),
                        f"/system/bin/{name}",
                    )

    def test_passes_through_absolute_path(self) -> None:
        self.assertEqual(
            android._resolve_android_binary("/usr/bin/dumpsys"),
            "/usr/bin/dumpsys",
        )

    def test_passes_through_unknown_binary(self) -> None:
        # An unknown name must NOT be auto-prefixed; we don't want to risk
        # shadowing a Termux binary of the same name.
        self.assertEqual(
            android._resolve_android_binary("python3"),
            "python3",
        )

    def test_passes_through_when_system_bin_missing(self) -> None:
        # When /system/bin doesn't exist (e.g. on a dev workstation), the
        # name is returned unchanged so subprocess.run reports ENOENT in
        # the usual way.
        with patch.object(os.path, "isfile", return_value=False):
            self.assertEqual(android._resolve_android_binary("dumpsys"), "dumpsys")


class RunCommandResolvesFirstArgTests(unittest.TestCase):
    def test_run_command_resolves_first_arg(self) -> None:
        captured: dict[str, list[str]] = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            class FakeCompleted:
                returncode = 0
                stdout = "ok"
                stderr = ""
            return FakeCompleted()

        with patch.object(os.path, "isfile", side_effect=_exists), \
             patch.object(os, "access", side_effect=_access), \
             patch("agent.android.subprocess.run", side_effect=fake_run):
            res = android.run_command(["dumpsys", "window", "windows"], timeout=2)
        self.assertEqual(captured["cmd"][0], "/system/bin/dumpsys")
        self.assertEqual(res.returncode, 0)


class RunAndroidCommandRootFallbackTests(unittest.TestCase):
    """``run_android_command`` retries via su on Permission Denial."""

    def test_falls_back_to_root_on_permission_denial(self) -> None:
        call_order: list[str] = []

        def fake_run_command(args, *, timeout):  # noqa: ARG001
            call_order.append("plain")
            return android.CommandResult(
                tuple(args), 255, "",
                "Security exception: Permission Denial: getCurrentUser() from pid=10158",
            )

        def fake_run_root(args, *, root_tool, timeout):  # noqa: ARG001
            call_order.append("root")
            return android.CommandResult(tuple(args), 0, "key=value", "")

        with patch.object(android, "run_command", side_effect=fake_run_command), \
             patch.object(android, "run_root_command", side_effect=fake_run_root), \
             patch.object(android, "detect_root", return_value=android.RootInfo(True, "su", "")):
            res = android.run_android_command(["settings", "list", "global"])
        self.assertEqual(call_order, ["plain", "root"])
        self.assertTrue(res.ok)
        self.assertEqual(res.stdout, "key=value")

    def test_no_root_fallback_when_unprivileged_succeeds(self) -> None:
        def fake_run_command(args, *, timeout):  # noqa: ARG001
            return android.CommandResult(tuple(args), 0, "ok", "")

        called_root = []
        def fake_run_root(*args, **kwargs):  # noqa: ARG001
            called_root.append(True)
            return android.CommandResult((), 0, "", "")

        with patch.object(android, "run_command", side_effect=fake_run_command), \
             patch.object(android, "run_root_command", side_effect=fake_run_root):
            res = android.run_android_command(["settings", "list", "global"])
        self.assertTrue(res.ok)
        self.assertEqual(called_root, [])

    def test_prefer_root_skips_unprivileged(self) -> None:
        called: list[str] = []

        def fake_run_command(args, *, timeout):  # noqa: ARG001
            called.append("plain")
            return android.CommandResult(tuple(args), 0, "", "")

        def fake_run_root(args, *, root_tool, timeout):  # noqa: ARG001
            called.append("root")
            return android.CommandResult(tuple(args), 0, "root-stdout", "")

        with patch.object(android, "run_command", side_effect=fake_run_command), \
             patch.object(android, "run_root_command", side_effect=fake_run_root), \
             patch.object(android, "detect_root", return_value=android.RootInfo(True, "su", "")):
            res = android.run_android_command(["dumpsys", "window"], prefer_root=True)
        self.assertEqual(called, ["root"])
        self.assertEqual(res.stdout, "root-stdout")


if __name__ == "__main__":
    unittest.main()
