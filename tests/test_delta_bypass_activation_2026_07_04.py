"""Lime live Delta bypass flow tests."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.lime_delta_key_bypass import (  # noqa: E402
    is_first_stagger_package,
    parse_bypass_origin,
    parse_bypass_token,
    reset_session_state,
    run_lime_delta_bypass_flow,
)


class ParseBypassTokenTests(unittest.TestCase):
    def test_link(self) -> None:
        link = "https://rejoin.deng.my.id/bypass?token=abc123"
        self.assertEqual(parse_bypass_token(link), "abc123")
        self.assertEqual(parse_bypass_origin(link), "https://rejoin.deng.my.id")


class FirstPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_session_state()

    def test_first_package_only(self) -> None:
        cfg = {
            "roblox_packages": [
                {"package": "com.moons.litesc", "enabled": True},
                {"package": "com.moons.litesd", "enabled": True},
            ]
        }
        self.assertTrue(is_first_stagger_package("com.moons.litesc", cfg))
        self.assertFalse(is_first_stagger_package("com.moons.litesd", cfg))


class LimeFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_session_state()

    def test_full_flow_success(self) -> None:
        cfg = {
            "roblox_packages": [{"package": "com.moons.litesc", "enabled": True}],
            "package_keys": {"global": "", "per_package": {}},
        }
        with patch("agent.lime_delta_key_bypass.lime_detection_enabled", return_value=True):
            with patch(
                "agent.lime_delta_key_bypass._wait_for_delta_dialog",
                return_value=(True, "Welcome Back Enter key Receive Key"),
            ):
                with patch("agent.lime_delta_key_bypass._tap_receive_key", return_value=True):
                    with patch(
                        "agent.lime_delta_key_bypass._capture_token_from_device",
                        return_value=(
                            "tok123",
                            "https://rejoin.deng.my.id",
                            "logcat",
                        ),
                    ):
                        with patch(
                            "agent.lime_delta_key_bypass._force_stop_package",
                            return_value=True,
                        ):
                            with patch(
                                "agent.lime_delta_key_bypass.fetch_bypass_license",
                                return_value=(True, {"ok": True, "key": "DELTA-KEY-1"}),
                            ):
                                with patch(
                                    "agent.lime_delta_key_bypass.activate_bypass_license",
                                    return_value=(True, {"ok": True}),
                                ):
                                    with patch(
                                        "agent.lime_delta_key_bypass._write_license",
                                        return_value=True,
                                    ):
                                        with patch(
                                            "agent.lime_delta_key_bypass._persist_key"
                                        ):
                                            out = run_lime_delta_bypass_flow(
                                                "com.moons.litesc", cfg, force=True
                                            )
        self.assertTrue(out.get("relaunch_requested"))
        self.assertEqual(out.get("phase"), "done")
        self.assertEqual(out.get("packages_written"), ["com.moons.litesc"])

    def test_skips_second_clone(self) -> None:
        cfg = {
            "roblox_packages": [
                {"package": "com.moons.litesc", "enabled": True},
                {"package": "com.moons.litesd", "enabled": True},
            ]
        }
        with patch("agent.lime_delta_key_bypass.lime_detection_enabled", return_value=True):
            out = run_lime_delta_bypass_flow("com.moons.litesd", cfg)
        self.assertEqual(out.get("last_error"), "not_first_stagger_package")


class PerformRejoinHookTests(unittest.TestCase):
    def test_hook_relaunches_after_bypass(self) -> None:
        import agent.launcher as launcher
        import agent.test_latest2_runtime_patch as rtp

        calls: list[str] = []
        saved = launcher.perform_rejoin

        def fake_rejoin(cfg, *, reason="manual", package_entry=None, no_force_stop=False):
            calls.append(reason)
            return MagicMock(ok=True)

        launcher.perform_rejoin = fake_rejoin
        setattr(launcher, "_test_latest2_lime_bypass_patched", False)
        try:
            rtp._patch_delta_bypass_at_start()
            with patch("agent.lime_delta_key_bypass.run_lime_delta_bypass_flow") as flow:
                flow.return_value = {"relaunch_requested": True}
                launcher.perform_rejoin(
                    {"roblox_packages": [{"package": "com.moons.litesc", "enabled": True}]},
                    reason="start",
                    package_entry={"package": "com.moons.litesc"},
                )
            self.assertEqual(calls.count("start"), 2)
        finally:
            launcher.perform_rejoin = saved


if __name__ == "__main__":
    unittest.main()
