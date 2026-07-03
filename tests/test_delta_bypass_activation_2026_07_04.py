"""Delta bypass token activation (Lime ``/bypass?token=`` compatible)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.delta_bypass_store import (  # noqa: E402
    activate_executor_license,
    mint_bypass_token,
    redeem_bypass_token,
)
from agent.lime_delta_key_bypass import (  # noqa: E402
    activate_delta_bypass_if_configured,
    parse_bypass_token,
)


class ParseBypassTokenTests(unittest.TestCase):
    def test_plain_token(self) -> None:
        self.assertEqual(parse_bypass_token("abc123"), "abc123")

    def test_full_link(self) -> None:
        link = "https://rejoin.deng.my.id/bypass?token=tok_xyz"
        self.assertEqual(parse_bypass_token(link), "tok_xyz")


class DeltaBypassStoreTests(unittest.TestCase):
    def test_mint_redeem_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "tokens.json"
            with patch("agent.delta_bypass_store._store_path", return_value=store):
                token = mint_bypass_token("DELTA-KEY-TEST")
                ok, row = redeem_bypass_token(token)
                self.assertTrue(ok)
                self.assertEqual(row.get("key"), "DELTA-KEY-TEST")
                ok2, row2 = redeem_bypass_token(token)
                self.assertFalse(ok2)
                self.assertEqual(row2.get("error"), "already_used")


class DeltaBypassActivationTests(unittest.TestCase):
    def test_activation_writes_config(self) -> None:
        import agent.lime_delta_key_bypass as ldb

        ldb._activated_once = False
        cfg = {
            "roblox_packages": [{"package": "com.moons.litesc", "enabled": True}],
            "delta_bypass": {"token": "testtok"},
            "package_keys": {"global": "", "per_package": {}},
        }
        bypass_resp = {"ok": True, "key": "DELTA-KEY-999"}
        activate_resp = {"ok": True, "activated": True}

        with patch("agent.lime_delta_key_bypass.lime_detection_enabled", return_value=True):
            with patch.object(ldb, "fetch_bypass_license", return_value=(True, bypass_resp)):
                with patch.object(ldb, "activate_bypass_license", return_value=(True, activate_resp)):
                    with patch.object(ldb, "_write_license_to_packages", return_value=["com.moons.litesc"]):
                        with patch.object(ldb, "_persist_config_key"):
                            out = ldb.activate_delta_bypass_if_configured(cfg, force=True)
        self.assertEqual(out.get("last_error"), None, out)
        self.assertEqual(out.get("activation_count"), 1)
        self.assertEqual(out.get("packages_written"), ["com.moons.litesc"])


class LicenseApiBypassRouteTests(unittest.TestCase):
    def test_activate_route(self) -> None:
        ok, payload = activate_executor_license("KEY1", hwid="abc")
        self.assertTrue(ok)
        self.assertTrue(payload.get("activated"))


if __name__ == "__main__":
    unittest.main()
