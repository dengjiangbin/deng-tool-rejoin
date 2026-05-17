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
        ):
            self.assertIn(key, probe, msg=f"missing key {key!r}")
        self.assertEqual(probe["probe_version"], P.PROBE_VERSION)
        self.assertIsInstance(probe["errors"], list)
        self.assertIsInstance(probe["packages"], dict)


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
