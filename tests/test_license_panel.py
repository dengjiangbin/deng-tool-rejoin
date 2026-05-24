"""Tests for agent/license_panel.py (tests 31-46)."""

import unittest

from agent.license_panel import (
    BUTTON_GENERATE,
    BUTTON_KEY_STATS,
    BUTTON_REDEEM,
    BUTTON_RESET_HWID,
    BUTTON_SELECT_VERSION,
    PANEL_LOGO_URL,
    SLASH_GROUP,
    build_generate_limit_response,
    build_generate_success_response,
    build_key_list_response,
    build_not_owner_response,
    build_panel_buttons,
    build_panel_embed,
    build_redeem_error_response,
    build_redeem_success_response,
    build_reset_active_warning_response,
    build_reset_limit_response,
    build_reset_success_response,
    get_slash_command_specs,
)


class PanelEmbedTests(unittest.TestCase):
    """Test 31-33: panel embed structure."""

    _PANEL_LINES = (
        "> \U0001f511 Generate Key",
        "> Take you to our portal to generate the keys.",
        "> \u267b\ufe0f Reset HWID",
        "> Move key to new device, 5 mins cooldown.",
        "> \U0001f39f\ufe0f Redeem Key",
        "> Make an existing key your own.",
        "> \U0001f4ca Key Stats",
        "> View status and export keys.",
        "> \U0001f4e6 Select Version",
        "> Choose which package version to install.",
    )

    def test_panel_embed_title(self):
        """Test 31 – embed title is correct."""
        embed = build_panel_embed()
        self.assertEqual(embed["title"], "DENG Tool: Rejoin Panel")

    def test_panel_embed_uses_description_not_inline_fields(self):
        """Panel uses one compact description block — no 3-column inline fields."""
        embed = build_panel_embed()
        self.assertIn("description", embed)
        self.assertNotIn("fields", embed)

    def test_panel_embed_description_copy(self):
        embed = build_panel_embed()
        desc = embed["description"]
        self.assertIn("Manage your key and package version seamlessly with our automated system.", desc)
        self.assertIn("Select an option below to get started:", desc)
        for line in self._PANEL_LINES:
            self.assertIn(line, desc)

    def test_panel_embed_footer(self):
        embed = build_panel_embed()
        self.assertEqual(
            embed["footer"]["text"],
            "DENG Tool \u2022 https://tool.deng.my.id \u2022 Secure & Automated",
        )

    def test_panel_embed_uses_website_transparent_logo_without_timestamp(self):
        embed = build_panel_embed()
        self.assertEqual(embed["thumbnail"]["url"], PANEL_LOGO_URL)
        self.assertEqual(PANEL_LOGO_URL, "https://tool.deng.my.id/public/img/deng-logo.png")
        self.assertNotIn("timestamp", embed)

    def test_panel_embed_no_old_limit_or_long_paragraphs(self):
        import json as _json

        embed = build_panel_embed()
        blob = _json.dumps(embed).lower()
        for forbidden in (
            "limited to 5 resets every 24 hours",
            "5 resets every 24 hours",
            "5/day",
            "store it somewhere safe",
            "only shown once",
            "all responses are private",
        ):
            self.assertNotIn(forbidden, blob)

    def test_panel_embed_no_plus_or_kaeru_wording(self):
        import json as _json

        embed = build_panel_embed()
        blob = _json.dumps(embed)
        self.assertNotIn("Plus", blob)
        self.assertNotIn("Kaeru", blob)
        self.assertNotIn("KAERU", blob)

    def test_panel_embed_color_is_brand_blue(self):
        """Test 33 – embed color is brand blue 0x2F80ED."""
        embed = build_panel_embed()
        self.assertEqual(embed["color"], 0x2F80ED)


class PanelButtonTests(unittest.TestCase):
    """Test 34-36: panel buttons structure."""

    def test_panel_has_five_buttons(self):
        """Test 34 – action row contains five buttons (incl. Select Version)."""
        components = build_panel_buttons()
        self.assertEqual(len(components), 1)
        row = components[0]
        self.assertEqual(row["type"], 1)
        self.assertEqual(len(row["components"]), 5)

    def test_button_custom_ids(self):
        """Test 35 – button custom_ids match constants."""
        components = build_panel_buttons()
        buttons = components[0]["components"]
        generate = buttons[0]
        self.assertEqual(generate["label"], "Generate Key")
        self.assertEqual(generate["style"], 5)
        self.assertEqual(generate["url"], "https://tool.deng.my.id")
        self.assertNotIn("custom_id", generate)
        ids = [btn["custom_id"] for btn in buttons[1:]]
        self.assertEqual(
            ids,
            [
                BUTTON_RESET_HWID,
                BUTTON_REDEEM,
                BUTTON_KEY_STATS,
                BUTTON_SELECT_VERSION,
            ],
        )

    def test_buttons_not_disabled_by_default(self):
        """Test 36 – all buttons are enabled (not disabled) by default."""
        components = build_panel_buttons()
        for btn in components[0]["components"]:
            self.assertFalse(btn.get("disabled", False))


class PanelResponseTests(unittest.TestCase):
    """Test 37-41: response builders are ephemeral and contain correct data."""

    def test_generate_success_is_ephemeral(self):
        """Test 37 – generate success response is ephemeral."""
        resp = build_generate_success_response("DENG-8F3A-B3C4-D5E6-44F0")
        self.assertTrue(resp.get("ephemeral"))

    def test_generate_success_contains_full_key(self):
        """Test 38 – full key appears in copy-friendly message content."""
        full_key = "DENG-8F3A-B3C4-D5E6-44F0"
        resp = build_generate_success_response(full_key)
        self.assertIn(full_key, resp.get("content", ""))
        self.assertNotIn(full_key, resp["embed"]["description"])

    def test_generate_limit_response_is_ephemeral(self):
        """Test 39 – generate limit response is ephemeral."""
        resp = build_generate_limit_response(max_keys=1)
        self.assertTrue(resp.get("ephemeral"))

    def test_reset_success_is_ephemeral(self):
        """Test 40 – reset success response is ephemeral."""
        resp = build_reset_success_response()
        self.assertTrue(resp.get("ephemeral"))

    def test_redeem_success_contains_full_key_not_ellipsis(self):
        """Test 41 – redeem success shows full key for copy (no … mask)."""
        full_key = "DENG-8F3A-B3C4-D5E6-44F0"
        resp = build_redeem_success_response(full_key)
        content = resp.get("content", "")
        self.assertIn(full_key, content)
        self.assertNotIn("...", content)
        self.assertNotIn(full_key, resp["embed"]["description"])


class PanelResponseSecurityTests(unittest.TestCase):
    """Test 42-44: copy views show full key when export/plaintext exists."""

    def test_redeem_success_includes_full_key_for_copy(self):
        """Test 42 – redeem success includes the full key string in content."""
        full = "DENG-8F3A-B3C4-D5E6-44F0"
        resp = build_redeem_success_response(full)
        self.assertIn(full, resp.get("content", ""))
        self.assertNotIn("...", resp.get("content", ""))

    def test_key_list_response_shows_full_key_when_plaintext_available(self):
        """Test 43 – key list puts full key in content when full_key_plaintext is set."""
        records = [{
            "masked_key": "DENG-8F3A...44F0",
            "full_key_plaintext": "DENG-8F3A-B3C4-D5E6-44F0",
            "status": "active",
            "bound_device": "Pixel",
        }]
        resp = build_key_list_response(records)
        self.assertIn("DENG-8F3A-B3C4-D5E6-44F0", resp.get("content", ""))
        desc = resp["embed"]["description"]
        self.assertNotIn("DENG-8F3A...44F0", desc)

    def test_key_list_without_plaintext_explains_not_copyable(self):
        records = [
            {"masked_key": "DENG-8F3A...44F0", "status": "active", "bound_device": "Pixel"},
        ]
        resp = build_key_list_response(records)
        desc = resp["embed"]["description"]
        self.assertIn("not recoverable", desc.lower())
        self.assertIn("DENG-8F3A...44F0", desc)
        self.assertNotIn("Recover Full Key", desc)

    def test_not_owner_response_is_ephemeral(self):
        """Test 44 – not-owner response is ephemeral."""
        resp = build_not_owner_response()
        self.assertTrue(resp.get("ephemeral"))


class PanelSlashCommandSpecTests(unittest.TestCase):
    """Test 45-46: slash command spec coverage."""

    def test_slash_specs_include_required_commands(self):
        """Test 45 – slash specs include set_channel, post, refresh, status, clear."""
        specs = get_slash_command_specs()
        names = {s["name"] for s in specs}
        for cmd in ("set_channel", "post", "refresh", "status", "clear"):
            self.assertIn(cmd, names, msg=f"Missing slash command spec: {cmd!r}")

    def test_slash_group_name(self):
        """Test 46 – all specs belong to the correct slash group."""
        specs = get_slash_command_specs()
        for spec in specs:
            self.assertEqual(spec["group"], SLASH_GROUP)


if __name__ == "__main__":
    unittest.main()
