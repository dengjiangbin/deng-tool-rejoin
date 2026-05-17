"""Tests for the live-debug probe module.

Network/Android tools are unavailable on the dev host; tests therefore
focus on the contract the rest of the system relies on:

* ``mask`` strips every kind of secret we know about
* ``collect_probe`` never raises and always returns a well-shaped dict
* ``save_probe`` writes a parseable JSON file
* ``upload_probe`` reports a clean failure when the install API URL is
  unconfigured (network is not hit during the test)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import probe as P  # noqa: E402


class MaskTests(unittest.TestCase):
    def test_masks_roblosecurity_cookie(self) -> None:
        text = "Cookie: .ROBLOSECURITY=_|WARNING:DO-NOT-SHARE; HttpOnly"
        masked = P.mask(text)
        self.assertNotIn("WARNING:DO-NOT-SHARE", masked)
        self.assertIn("<masked:roblosecurity>", masked)

    def test_masks_discord_webhook(self) -> None:
        text = "Posting to https://discord.com/api/webhooks/123/abcDEFghi end."
        masked = P.mask(text)
        self.assertNotIn("123/abcDEFghi", masked)
        self.assertIn("<masked:discord_webhook>", masked)

    def test_masks_github_pat(self) -> None:
        text = "token=ghp_abcdef1234567890ABCDEF1234567890XYZ end"
        masked = P.mask(text)
        self.assertNotIn("ghp_abcdef1234567890ABCDEF1234567890XYZ", masked)
        self.assertIn("<masked:github_pat>", masked)

    def test_masks_bearer_header(self) -> None:
        text = "Authorization: Bearer ey.JhbGciOi.JI"
        masked = P.mask(text)
        self.assertNotIn("ey.JhbGciOi.JI", masked)
        self.assertIn("<masked:bearer>", masked)

    def test_masks_license_key(self) -> None:
        text = "lic_AbCdEf0123456789xyzQQ-foobar"
        masked = P.mask(text)
        self.assertIn("<masked:license_key>", masked)

    def test_keeps_plain_text_intact(self) -> None:
        text = "private url: https://www.roblox.com/games/123/Adopt-Me?privateServerLinkCode=abc-xyz"
        masked = P.mask(text)
        # private URLs are intentionally preserved
        self.assertIn("privateServerLinkCode=abc-xyz", masked)

    def test_handles_none_and_empty(self) -> None:
        self.assertEqual(P.mask(None), "")
        self.assertEqual(P.mask(""), "")


class CollectProbeShapeTests(unittest.TestCase):
    def test_collect_probe_returns_required_keys(self) -> None:
        # Every per-step call is guarded; even with no Android tools the
        # function must still return a well-shaped dict.
        probe = P.collect_probe()
        for key in (
            "probe_version",
            "captured_at_iso",
            "errors",
            "build",
            "device",
            "screen",
            "settings",
            "command_help",
            "config",
            "packages",
            "log_tail",
            # New: third-party tool evidence captures.
            "installed_packages",
            "third_party_evidence",
            "appops",
            "termux_shared_prefs",
            "getprop",
            "dumpsys_global",
            "logcat",
        ):
            self.assertIn(key, probe, msg=f"missing key {key!r}")
        self.assertEqual(probe["probe_version"], P.PROBE_VERSION)
        self.assertIsInstance(probe["errors"], list)
        self.assertIsInstance(probe["packages"], dict)
        # Third-party evidence shape contract.
        ip = probe["installed_packages"]
        self.assertIsInstance(ip, dict)
        self.assertIn("third_party_tools", ip)
        self.assertIn("clone_packages", ip)
        self.assertIsInstance(ip["third_party_tools"], list)
        self.assertIsInstance(ip["clone_packages"], list)
        te = probe["third_party_evidence"]
        self.assertIsInstance(te, dict)
        self.assertIn("targets", te)
        self.assertIn("shared_prefs", te)
        self.assertIn("pm_dump", te)


class ThirdPartyDiscoveryTests(unittest.TestCase):
    """``_capture_installed_packages`` must surface Kaeru-style tools."""

    def test_filters_third_party_tool_hints(self) -> None:
        from agent import android
        # Stub run_android_command to return a fake `pm list packages -3`.
        fake_stdout = "\n".join([
            "package:com.kaeru.app",                 # tool hint
            "package:com.example.todo",              # boring user app
            "package:com.moons.litesc",              # clone
            "package:com.applauncher.pro",           # tool hint
            "package:com.kaerushiki.helper",         # tool hint (shiki)
            "package:com.android.chrome",            # boring
            "package:io.parallel.space",             # tool hint
        ])
        class _FakeRes:
            ok = True
            returncode = 0
            stdout = fake_stdout
            stderr = ""

        errors: list = []
        with patch.object(android, "run_android_command",
                          return_value=_FakeRes()):
            res = P._capture_installed_packages(errors)
        self.assertEqual(res["third_party_count"], 7)
        # Tool-hint matches MUST include kaeru, applauncher, kaerushiki,
        # parallel — but NOT chrome or todo.
        tools = set(res["third_party_tools"])
        self.assertIn("com.kaeru.app", tools)
        self.assertIn("com.applauncher.pro", tools)
        self.assertIn("com.kaerushiki.helper", tools)
        self.assertIn("io.parallel.space", tools)
        self.assertNotIn("com.android.chrome", tools)
        self.assertNotIn("com.example.todo", tools)
        # Clone packages MUST include the moons clone.
        self.assertIn("com.moons.litesc", res["clone_packages"])

    def test_raw_output_capped(self) -> None:
        """``pm_list_third_party_raw`` must be capped so a phone with
        500 installed packages doesn't blow up the upload size."""
        from agent import android
        big = "package:com.foo\n" * 50_000  # ~750 KB
        class _Res:
            ok = True
            returncode = 0
            stdout = big
            stderr = ""
        with patch.object(android, "run_android_command", return_value=_Res()):
            res = P._capture_installed_packages([])
        self.assertLessEqual(len(res["pm_list_third_party_raw"]), 60_000)

    def test_handles_run_error_without_raising(self) -> None:
        from agent import android
        with patch.object(android, "run_android_command",
                          side_effect=RuntimeError("boom")):
            res = P._capture_installed_packages([])
        self.assertEqual(res["third_party_count"], 0)
        self.assertEqual(res["third_party_tools"], [])
        self.assertEqual(res["clone_packages"], [])


class ThirdPartyEvidenceTests(unittest.TestCase):
    """``_capture_kaeru_evidence`` must dump prefs + pm dump for every target."""

    def test_dumps_prefs_and_permissions_for_each_target(self) -> None:
        from agent import android
        third_party = {
            "third_party_tools": ["com.kaeru.app"],
            "clone_packages": ["com.moons.litesc"],
        }
        recorded: list[str] = []
        class _Res:
            def __init__(self, stdout="ok"):
                self.ok = True
                self.returncode = 0
                self.stdout = stdout
                self.stderr = ""
        def fake_run(args, **kwargs):  # noqa: ANN001, ANN002
            recorded.append(" ".join(args))
            return _Res("fake_output")
        with patch.object(android, "run_android_command", side_effect=fake_run), \
             patch.object(android, "run_root_command", side_effect=fake_run), \
             patch.object(android, "detect_root",
                          return_value=type("R", (), {"available": False})()):
            res = P._capture_kaeru_evidence(third_party, [])
        self.assertEqual(
            sorted(res["targets"]),
            ["com.kaeru.app", "com.moons.litesc"],
        )
        # pm dump must have been recorded for every target.
        for pkg in ("com.kaeru.app", "com.moons.litesc"):
            self.assertIn(pkg, res["pm_dump"])
            self.assertIn(pkg, res["shared_prefs"])


class GetpropFilterTests(unittest.TestCase):
    """Filter MUST keep window/freeform-relevant props and drop noise."""

    def test_filter_keeps_window_props_drops_unrelated(self) -> None:
        from agent import android
        raw = "\n".join([
            "[ro.product.cpu.abi]: [arm64-v8a]",       # drop
            "[persist.wm.disable_explicit_size_freeze]: [1]",  # keep
            "[ro.config.low_ram]: [false]",            # keep
            "[net.dns1]: [8.8.8.8]",                   # drop
            "[ro.build.version.sdk]: [33]",            # keep (build)
            "[sys.usb.config]: [adb]",                 # drop
            "[persist.sys.freeform_window_management]: [true]",  # keep
            "[ro.surface_flinger.start_graphics_allocator_service]: [true]",  # keep (surface)
        ])
        class _R:
            ok = True
            returncode = 0
            stdout = raw
            stderr = ""
        with patch.object(android, "run_android_command", return_value=_R()):
            res = P._capture_getprop([])
        self.assertIn("persist.wm.disable_explicit_size_freeze", res["raw_filtered"])
        self.assertIn("persist.sys.freeform_window_management", res["raw_filtered"])
        self.assertIn("ro.config.low_ram", res["raw_filtered"])
        self.assertIn("ro.build.version.sdk", res["raw_filtered"])
        # The unrelated lines must be dropped.
        self.assertNotIn("net.dns1", res["raw_filtered"])
        self.assertNotIn("sys.usb.config", res["raw_filtered"])
        # all_count is the TOTAL line count, not the filtered count.
        self.assertEqual(res["all_count"], 8)


class SaveProbeTests(unittest.TestCase):
    def test_save_probe_writes_parseable_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(P, "PROBE_DIR", Path(tmp)):
                path = P.save_probe({"probe_version": 1, "captured_at_iso": "x", "errors": []})
            self.assertTrue(path.is_file())
            self.assertTrue(path.name.startswith("probe-"))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["probe_version"], 1)


class UploadProbeTests(unittest.TestCase):
    def test_upload_reports_missing_api(self) -> None:
        # Force both env override and on-disk install_api to be unset.
        with patch.dict(os.environ, {"DENG_REJOIN_INSTALL_API": ""}, clear=False):
            with patch.object(P, "_resolve_install_api", return_value=""):
                ok, info = P.upload_probe({"probe_version": 1})
        self.assertFalse(ok)
        self.assertIn("install API URL not configured", info)


if __name__ == "__main__":
    unittest.main()
