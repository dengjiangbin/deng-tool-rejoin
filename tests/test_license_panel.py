"""Tests for agent/license_panel.py (tests 31-46)."""

import unittest

from agent.license_panel import (
    BUTTON_GENERATE,
    BUTTON_KEY_STATS,
    BUTTON_REDEEM,
    BUTTON_RESET_HWID,
    BUTTON_SELECT_VERSION,
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

    def test_panel_embed_title(self):
        """Test 31 – embed title is correct."""
        embed = build_panel_embed()
        self.assertEqual(embed["title"], "DENG Tool: Rejoin Panel")

    def test_panel_embed_has_four_fields(self):
        """Test 32 – embed has exactly 4 instruction fields."""
        embed = build_panel_embed()
        self.assertEqual(len(embed["fields"]), 4)

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
        ids = [btn["custom_id"] for btn in components[0]["components"]]
        self.assertEqual(
            ids,
            [
                BUTTON_GENERATE,
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
            self.assertFalse(btn["disabled"])


class PanelResponseTests(unittest.TestCase):
    """Test 37-41: response builders are ephemeral and contain correct data."""

    def test_generate_success_is_ephemeral(self):
        """Test 37 – generate success response is ephemeral."""
        resp = build_generate_success_response("DENG-8F3A-B3C4-D5E6-44F0")
        self.assertTrue(resp.get("ephemeral"))

    def test_generate_success_contains_full_key(self):
        """Test 38 – full key appears in the generate success response."""
        full_key = "DENG-8F3A-B3C4-D5E6-44F0"
        resp = build_generate_success_response(full_key)
        desc = resp["embed"]["description"]
        self.assertIn(full_key, desc)

    def test_generate_limit_response_is_ephemeral(self):
        """Test 39 – generate limit response is ephemeral."""
        resp = build_generate_limit_response(max_keys=1)
        self.assertTrue(resp.get("ephemeral"))

    def test_reset_success_is_ephemeral(self):
        """Test 40 – reset success response is ephemeral."""
        resp = build_reset_success_response()
        self.assertTrue(resp.get("ephemeral"))

    def test_redeem_success_contains_masked_key(self):
        """Test 41 – redeem success response contains masked key."""
        resp = build_redeem_success_response("DENG-8F3A...44F0")
        desc = resp["embed"]["description"]
        self.assertIn("DENG-8F3A...44F0", desc)


class PanelResponseSecurityTests(unittest.TestCase):
    """Test 42-44: security — full keys never appear outside generate response."""

    def test_redeem_success_does_not_contain_full_key(self):
        """Test 42 – redeem success only shows masked key, not original."""
        full = "DENG-8F3A-B3C4-D5E6-44F0"
        masked = "DENG-8F3A...44F0"
        resp = build_redeem_success_response(masked)
        import json
        serialized = json.dumps(resp)
        self.assertNotIn("B3C4", serialized)
        self.assertNotIn("D5E6", serialized)

    def test_key_list_response_uses_masked_keys(self):
        """Test 43 – key list response contains only masked keys."""
        records = [{"masked_key": "DENG-8F3A...44F0", "status": "active", "bound_device": "Pixel"}]
        resp = build_key_list_response(records)
        import json
        serialized = json.dumps(resp)
        self.assertIn("DENG-8F3A...44F0", serialized)

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
