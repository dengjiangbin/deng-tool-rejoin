"""Tests for mobile-friendly panel copy and Key Stats cleanup."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.license_panel import build_panel_embed, build_reset_selector_embed
from agent.license_store import LocalJsonLicenseStore
from bot.cog_license_panel import KeyStatsDownloadButton, KeyStatsView, PanelView


def _make_store(tmp_dir: str) -> LocalJsonLicenseStore:
    return LocalJsonLicenseStore(Path(tmp_dir) / "license_store.json")


class TestPanelMobileCopy(unittest.TestCase):
    def test_panel_description_block(self) -> None:
        embed = build_panel_embed()
        self.assertEqual(embed["title"], "DENG Tool: Rejoin Panel")
        self.assertEqual(embed["footer"]["text"], "DENG Tool • https://tool.deng.my.id • Secure & Automated")
        self.assertNotIn("fields", embed)
        self.assertNotIn("timestamp", embed)
        self.assertIn("> ♻️ Reset HWID\n> Unbind your keys from the current device, 5-minute cooldown.", embed["description"])
        self.assertNotIn("Move key to new device, 5 mins cooldown.", embed["description"])


class TestResetHwidWording(unittest.TestCase):
    def test_reset_selector_has_no_daily_limit_footer(self) -> None:
        payload = build_reset_selector_embed([{
            "key_id": "k1",
            "masked_key": "DENG-AB12...CD34",
            "full_key_plaintext": "DENG-AB12-CD34-EF56-7890",
            "active_binding": True,
            "device_model": "Pixel",
            "device_label": "",
        }])
        footer = payload["embed"].get("footer", {}).get("text", "")
        blob = (payload["embed"].get("description") or "") + footer
        for forbidden in (
            "Limited to 5 resets",
            "5 resets every 24 hours",
            "5/day",
            "per 24 hours",
        ):
            self.assertNotIn(forbidden, blob)
        self.assertIn("5 minute", footer.lower())


class TestKeyStatsRecoverRemoval(unittest.TestCase):
    def test_key_stats_view_has_no_recover_button(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            store.get_or_create_user("900")
            store.create_key_for_user("900")
            view = KeyStatsView(store, "900")
            labels = [getattr(c, "label", "") for c in view.children]
            self.assertIn("Download Keys", labels)
            self.assertNotIn("Recover Full Key", labels)
            custom_ids = [getattr(c, "custom_id", None) for c in view.children]
            self.assertNotIn("license_panel:ks_recover", custom_ids)

    def test_key_stats_module_has_no_recover_handler(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "bot" / "cog_license_panel.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("class KeyStatsRecoverExportButton", source)
        self.assertNotIn("class RecoverKeyExportModal", source)
        self.assertNotIn("license_panel:ks_recover", source)


class TestKeyStatsExportPreserved(unittest.IsolatedAsyncioTestCase):
    async def test_download_keys_still_works(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            uid = 901
            store.get_or_create_user(str(uid))
            store.create_key_for_user(str(uid))
            view = KeyStatsView(store, str(uid))
            dl = next(c for c in view.children if isinstance(c, KeyStatsDownloadButton))
            inter = MagicMock()
            inter.user = MagicMock()
            inter.user.id = uid
            inter.response = MagicMock()
            inter.response.send_message = AsyncMock()
            await dl.callback(inter)
            inter.response.send_message.assert_called_once()
            _, kwargs = inter.response.send_message.call_args
            self.assertIn("file", kwargs)


class TestPanelButtonsPreserved(unittest.TestCase):
    def test_panel_view_still_has_five_buttons(self) -> None:
        with TemporaryDirectory() as tmp:
            view = PanelView(_make_store(tmp))
            self.assertEqual(len(view.children), 5)
            self.assertEqual(
                [getattr(c, "label", "") for c in view.children],
                ["Generate Key", "Reset HWID", "Redeem Key", "Key Stats", "Select Version"],
            )


if __name__ == "__main__":
    unittest.main()
