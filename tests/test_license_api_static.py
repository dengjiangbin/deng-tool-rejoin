"""Static asset route for license API (logo)."""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


class TestLicenseApiLogoAsset(unittest.TestCase):
    def test_denghub_logo_served_without_auth(self) -> None:
        from bot.license_api import _wsgi_app

        logo = PROJECT / "assets" / "denghub_logo.png"
        self.assertTrue(logo.is_file(), "expected assets/denghub_logo.png in repo")

        captured: dict = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            captured["status"] = status
            captured["headers"] = dict(headers)

        env = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/assets/denghub_logo.png",
            "wsgi.input": io.BytesIO(),
            "CONTENT_LENGTH": "0",
        }
        body = b"".join(_wsgi_app(env, start_response))
        self.assertIn("200", captured.get("status", ""))
        self.assertGreater(len(body), 1000)
        self.assertEqual(captured["headers"].get("Content-Type"), "image/png")


if __name__ == "__main__":
    unittest.main()
