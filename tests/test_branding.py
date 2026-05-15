"""Branding helper tests (hosted logo URL only)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.branding import (
    apply_branding,
    apply_branding_to_embed_dict,
    get_branding_logo_url,
)


class TestBranding(unittest.TestCase):
    def tearDown(self) -> None:
        pass

    def test_no_url_leaves_embed_untouched(self) -> None:
        with patch.dict(
            os.environ,
            {"DENG_BRANDING_LOGO_URL": "", "LICENSE_API_PUBLIC_URL": ""},
            clear=False,
        ):
            d = {"title": "X", "description": "Y"}
            apply_branding_to_embed_dict(d)
            self.assertNotIn("thumbnail", d)

    def test_url_sets_thumbnail(self) -> None:
        with patch.dict(
            os.environ,
            {"DENG_BRANDING_LOGO_URL": "https://example.com/logo.png"},
            clear=False,
        ):
            d: dict = {"title": "X"}
            apply_branding(d)
            self.assertEqual(d["thumbnail"]["url"], "https://example.com/logo.png")

    def test_get_branding_logo_url_strips(self) -> None:
        with patch.dict(os.environ, {"DENG_BRANDING_LOGO_URL": "  https://x/y  "}, clear=False):
            self.assertEqual(get_branding_logo_url(), "https://x/y")

    def test_license_api_public_url_appends_logo_path(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DENG_BRANDING_LOGO_URL": "",
                "LICENSE_API_PUBLIC_URL": "https://rejoin.example/tool",
            },
            clear=False,
        ):
            self.assertEqual(
                get_branding_logo_url(),
                "https://rejoin.example/tool/assets/denghub_logo.png",
            )

    def test_include_thumbnail_false_omits_logo(self) -> None:
        with patch.dict(
            os.environ,
            {"DENG_BRANDING_LOGO_URL": "https://example.com/logo.png"},
            clear=False,
        ):
            d: dict = {"title": "X"}
            apply_branding_to_embed_dict(d, include_thumbnail=False)
            self.assertNotIn("thumbnail", d)

    def test_include_thumbnail_false_drops_existing_thumbnail(self) -> None:
        with patch.dict(
            os.environ,
            {"DENG_BRANDING_LOGO_URL": "https://example.com/logo.png"},
            clear=False,
        ):
            d: dict = {"title": "X", "thumbnail": {"url": "https://old/wrong.png"}}
            apply_branding_to_embed_dict(d, include_thumbnail=False)
            self.assertNotIn("thumbnail", d)


if __name__ == "__main__":
    unittest.main()
