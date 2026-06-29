"""Regression tests for disconnect-code recovery + deeplink target resolution upgrade."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.roblox_disconnect_reasons import (
    format_lifecycle_dead_reason,
    internal_reason_for_disconnect_code,
)
from agent.url_utils import parse_expected_target_from_url


class DisconnectCodeReasonTests(unittest.TestCase):
    def test_all_known_codes_get_unique_internal_keys(self) -> None:
        self.assertEqual(internal_reason_for_disconnect_code(278), "idle_disconnect_278")
        self.assertEqual(internal_reason_for_disconnect_code(285), "disconnect_code_285")
        self.assertEqual(internal_reason_for_disconnect_code(268), "disconnect_code_268")
        self.assertEqual(internal_reason_for_disconnect_code(271), "disconnect_code_271")

    def test_format_lifecycle_includes_error_code_prompt(self) -> None:
        # Generic disconnect codes still render as the canonical "Error Code: N ..."
        text = format_lifecycle_dead_reason(
            "disconnect_code_268",
            "Sending disconnect with reason: 268",
        )
        self.assertIn("268", text)
        self.assertIn("Error Code", text)

    def test_code_285_renders_clean_lobby_reason(self) -> None:
        # probe p-630c95f7cc #2: 285 = left the map / stuck in lobby. The webhook
        # reason must be the plain phrase, NOT "Error Code: 285 <FLog junk>".
        text = format_lifecycle_dead_reason(
            "disconnect_code_285",
            "Error Code: 285 928940,b5re230,7 [FLog::Network] Sending disconnect with reason: 285",
        )
        self.assertEqual(text, "Account stays too long in the lobby")
        self.assertNotIn("Error Code", text)
        self.assertNotIn("285", text)


class DeeplinkParseTests(unittest.TestCase):
    def test_share_url_extracts_code_and_type(self) -> None:
        target = parse_expected_target_from_url(
            "https://www.roblox.com/share?code=ABC123&type=Server"
        )
        self.assertEqual(target.expected_private_code, "ABC123")
        self.assertEqual(target.expected_share_type, "Server")

    def test_roblox_share_links_path_form(self) -> None:
        target = parse_expected_target_from_url(
            "roblox://navigation/share_links/Server/MYCODE999"
        )
        self.assertEqual(target.expected_private_code, "MYCODE999")

    def test_games_start_place_id(self) -> None:
        target = parse_expected_target_from_url(
            "https://www.roblox.com/games/start?placeId=987654321"
        )
        self.assertEqual(target.expected_place_id, 987654321)


class ShareLinkResolveTests(unittest.TestCase):
    def test_enrich_resolves_share_code_to_place(self) -> None:
        from agent.roblox_target_resolver import enrich_expected_target
        from agent.url_utils import RobloxExpectedTarget

        base = RobloxExpectedTarget(
            original_url="https://www.roblox.com/share?code=TESTCODE&type=Server",
            expected_private_code="TESTCODE",
            expected_share_type="Server",
        )
        resolved = RobloxExpectedTarget(
            expected_place_id=111,
            expected_root_place_id=111,
            expected_universe_id=222,
            expected_private_code="TESTCODE",
            expected_share_type="Server",
        )
        with patch(
            "agent.roblox_target_resolver.resolve_share_link",
            return_value=resolved,
        ):
            out = enrich_expected_target(base)
        self.assertEqual(out.expected_place_id, 111)
        self.assertEqual(out.expected_universe_id, 222)


if __name__ == "__main__":
    unittest.main()
