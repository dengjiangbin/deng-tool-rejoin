"""Reply embeds must not get DENG Hub thumbnail (only the main panel post does)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.branding import apply_branding_to_embed_dict
from agent.license_panel import build_generate_success_response, build_panel_embed
from bot.cog_license_panel import _embed_from_payload


class PanelVersusReplyBrandingTests(unittest.TestCase):
    def test_main_panel_payload_gets_thumbnail_when_configured(self) -> None:
        d = build_panel_embed()
        with patch.dict(
            os.environ,
            {"DENG_BRANDING_LOGO_URL": "https://example.com/hub.png"},
            clear=False,
        ):
            apply_branding_to_embed_dict(d, include_thumbnail=True)
        self.assertEqual(d["thumbnail"]["url"], "https://example.com/hub.png")

    def test_ephemeral_reply_payload_has_no_thumbnail_with_same_env(self) -> None:
        with patch.dict(
            os.environ,
            {"DENG_BRANDING_LOGO_URL": "https://example.com/hub.png"},
            clear=False,
        ):
            embed = _embed_from_payload(
                build_generate_success_response("DENG-1111-2222-3333-4444")
            )
        self.assertNotIn("thumbnail", embed.to_dict())


if __name__ == "__main__":
    unittest.main()
