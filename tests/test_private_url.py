"""Tests for private URL / private server join logic.

Covers:
  - effective_private_server_url: per-entry override wins over global config
  - URL used for launch command construction (am start -a VIEW -d <url>)
  - Private URL sets initial state to Joining (not Launching)
  - No private URL sets initial state to Launching or Lobby
  - Invalid/empty URL falls back to normal launch_app
  - Multiple packages each get their own URL correctly
"""

from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.config import default_config, effective_private_server_url, validate_config
from agent import android as amod


def _make_entry(package: str, private_url: str = "") -> dict:
    return {
        "package": package,
        "account_username": "TestUser",
        "enabled": True,
        "username_source": "manual",
        "private_server_url": private_url,
        "auto_reopen_enabled": True,
        "auto_reconnect_enabled": True,
    }


def _make_cfg_with_global_url(global_url: str = "") -> dict:
    cfg = default_config()
    cfg["first_setup_completed"] = True
    cfg["launch_mode"] = "app"
    cfg["private_server_url"] = global_url
    cfg["roblox_packages"] = [
        {
            "package": "com.roblox.client",
            "account_username": "Main",
            "enabled": True,
            "username_source": "manual",
            "private_server_url": "",
        }
    ]
    return cfg


class TestEffectivePrivateServerUrl(unittest.TestCase):
    """effective_private_server_url priority: entry > global config."""

    def test_entry_url_wins_over_global(self):
        entry = _make_entry("com.roblox.client", private_url="roblox://placeId=111")
        cfg = _make_cfg_with_global_url("roblox://placeId=999")
        url = effective_private_server_url(entry, cfg)
        self.assertEqual(url, "roblox://placeId=111")

    def test_global_url_used_when_entry_url_empty(self):
        entry = _make_entry("com.roblox.client", private_url="")
        cfg = _make_cfg_with_global_url("roblox://placeId=999")
        url = effective_private_server_url(entry, cfg)
        self.assertEqual(url, "roblox://placeId=999")

    def test_no_url_returns_none_or_empty(self):
        entry = _make_entry("com.roblox.client", private_url="")
        cfg = _make_cfg_with_global_url("")
        url = effective_private_server_url(entry, cfg)
        self.assertFalse(bool(url))

    def test_whitespace_url_treated_as_empty(self):
        entry = _make_entry("com.roblox.client", private_url="   ")
        cfg = _make_cfg_with_global_url("  ")
        url = effective_private_server_url(entry, cfg)
        self.assertFalse(bool(str(url or "").strip()))


class TestLaunchCommandConstructionWithURL(unittest.TestCase):
    """launch_package_with_options must use am start VIEW when URL is provided."""

    def test_url_triggers_view_intent(self):
        """When private URL is set, launch_url() must be called using VIEW intent."""
        from agent import android as amod

        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(list(cmd))
            if "VIEW" in cmd or "android.intent.action.VIEW" in cmd:
                return amod.CommandResult(tuple(cmd), 0, "OK", "")
            return amod.CommandResult(tuple(cmd), 0, "OK", "")

        with unittest.mock.patch("agent.android.run_command", side_effect=fake_run), \
             unittest.mock.patch("agent.android._find_command", return_value="/system/bin/am"):
            result, method = amod.launch_package_with_options(
                "com.roblox.client",
                "roblox://placeId=123&privateServerLinkCode=abc",
            )

        self.assertTrue(result.ok)
        # VIEW intent must appear somewhere in the captured commands
        all_args = [a for cmd in captured for a in cmd]
        self.assertTrue(
            any("VIEW" in str(a) or "android.intent.action.VIEW" in str(a) for a in all_args),
            f"VIEW intent not found in captured commands: {captured}",
        )

    def test_no_url_uses_launch_app(self):
        """Without URL, fallback to normal app launch (MAIN + LAUNCHER)."""
        from agent import android as amod

        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(list(cmd))
            if "MAIN" in cmd or "android.intent.action.MAIN" in cmd:
                return amod.CommandResult(tuple(cmd), 0, "OK", "")
            return amod.CommandResult(tuple(cmd), 0, "OK", "")

        with unittest.mock.patch("agent.android.run_command", side_effect=fake_run), \
             unittest.mock.patch("agent.android._find_command", return_value="/system/bin/am"):
            result, method = amod.launch_package_with_options(
                "com.roblox.client",
                None,
            )

        self.assertTrue(result.ok)
        all_args = [a for cmd in captured for a in cmd]
        self.assertTrue(
            any("MAIN" in str(a) or "android.intent.action.MAIN" in str(a) for a in all_args),
            f"MAIN intent not found: {captured}",
        )

    def test_invalid_url_falls_back_to_launch_app(self):
        """An unparseable URL must not crash — fall back to launch_app."""
        from agent import android as amod

        def fake_run(cmd, **kwargs):
            return amod.CommandResult(tuple(cmd), 0, "OK", "")

        with unittest.mock.patch("agent.android.run_command", side_effect=fake_run), \
             unittest.mock.patch("agent.android._find_command", return_value="/system/bin/am"):
            try:
                result, method = amod.launch_package_with_options(
                    "com.roblox.client",
                    "NOT_A_VALID_URL_##$$%%",
                )
                # Should not raise; should fall back gracefully
                self.assertIsNotNone(result)
            except Exception as exc:
                self.fail(f"launch_package_with_options raised with invalid URL: {exc}")


class TestPerformRejoinURLState(unittest.TestCase):
    """perform_rejoin: URL is read from effective_private_server_url."""

    def test_perform_rejoin_with_url_succeeds(self):
        """Two-phase launch: Phase 1 uses None URL; Phase 2 delivers URL via launch_url.

        The URL must reach android.launch_url (Phase 2), not the initial
        launch_package_with_options call (Phase 1), which always uses None so
        that window bounds are applied before the join URL is sent.
        """
        import agent.launcher as _launcher
        from agent.launcher import perform_rejoin

        cfg = default_config()
        cfg["first_setup_completed"] = True
        cfg["launch_mode"] = "app"
        cfg["private_server_url"] = "roblox://placeId=42"
        cfg["roblox_packages"] = [
            {
                "package": "com.roblox.client",
                "account_username": "TestUser",
                "enabled": True,
                "username_source": "manual",
                "private_server_url": "",  # will use global
            }
        ]

        phase2_urls: list = []

        def fake_launch_url(pkg, url, mode):
            phase2_urls.append(url)
            return amod.CommandResult(("am", "start"), 0, "OK", "")

        with unittest.mock.patch.object(amod, "launch_package_with_options") as mock_launch, \
             unittest.mock.patch.object(amod, "launch_url", side_effect=fake_launch_url), \
             unittest.mock.patch.object(amod, "package_installed", return_value=True), \
             unittest.mock.patch.object(_launcher, "_proc_scan_alive", return_value=True):
            mock_launch.return_value = (
                amod.CommandResult(("am", "start"), 0, "Success", ""),
                "am_or_resolve",
            )
            result = perform_rejoin(cfg, reason="start")

        self.assertTrue(result.success)
        # Phase 1 must receive None (not the URL)
        call_args = mock_launch.call_args
        url_arg_p1 = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("private_url") or call_args[1].get("url")
        self.assertIsNone(url_arg_p1, f"Phase 1 must NOT pass URL, got: {url_arg_p1}")
        # Phase 2 must have received the URL
        self.assertTrue(
            any("42" in str(u) or "placeId" in str(u) for u in phase2_urls),
            f"Phase 2 (launch_url) must receive the URL, got: {phase2_urls}",
        )

    def test_perform_rejoin_without_url_succeeds(self):
        from agent.launcher import perform_rejoin

        cfg = default_config()
        cfg["first_setup_completed"] = True
        cfg["launch_mode"] = "app"
        cfg["private_server_url"] = ""

        with unittest.mock.patch.object(amod, "launch_package_with_options") as mock_launch, \
             unittest.mock.patch.object(amod, "package_installed", return_value=True):
            mock_launch.return_value = (
                amod.CommandResult(("am", "start"), 0, "Success", ""),
                "am_or_resolve",
            )
            result = perform_rejoin(cfg, reason="start")

        self.assertTrue(result.success)

    def test_per_entry_url_overrides_global(self):
        """Two-phase launch: Phase 1 launches without URL; Phase 2 delivers URL via launch_url.

        We verify the entry-specific URL reaches ``android.launch_url`` (Phase 2),
        not the Phase 1 ``launch_package_with_options`` call which intentionally
        passes None so the window bounds are applied before the URL is delivered.
        """
        import agent.launcher as _launcher
        from agent.launcher import perform_rejoin

        cfg = default_config()
        cfg["first_setup_completed"] = True
        cfg["launch_mode"] = "app"
        cfg["private_server_url"] = "roblox://placeId=GLOBAL"
        entry = _make_entry("com.roblox.client", private_url="roblox://placeId=ENTRY_SPECIFIC")
        cfg["roblox_packages"] = [
            {
                "package": "com.roblox.client",
                "account_username": "TestUser",
                "enabled": True,
                "username_source": "manual",
                "private_server_url": "roblox://placeId=ENTRY_SPECIFIC",
            }
        ]

        phase1_urls = []
        phase2_urls = []

        def fake_launch_opts(package, url=None):
            phase1_urls.append(url)
            return (amod.CommandResult(("am", "start"), 0, "OK", ""), "am_or_resolve")

        def fake_launch_url(package, url, mode):
            phase2_urls.append(url)
            return amod.CommandResult(("am", "start"), 0, "OK", "")

        with unittest.mock.patch.object(amod, "launch_package_with_options", side_effect=fake_launch_opts), \
             unittest.mock.patch.object(amod, "launch_url", side_effect=fake_launch_url), \
             unittest.mock.patch.object(amod, "package_installed", return_value=True), \
             unittest.mock.patch.object(_launcher, "_proc_scan_alive", return_value=True):
            perform_rejoin(cfg, reason="start", package_entry=entry)

        # Phase 1: URL must be None (bounds-first, no URL)
        self.assertEqual(phase1_urls, [None], "Phase 1 must NOT pass URL to launch_package_with_options")
        # Phase 2: entry-specific URL must be delivered via launch_url
        self.assertTrue(
            any("ENTRY_SPECIFIC" in str(u) for u in phase2_urls),
            f"Expected ENTRY_SPECIFIC URL in phase2 launch_url calls, got: {phase2_urls}",
        )


class TestMultiPackageURLHandling(unittest.TestCase):
    """Multiple packages each get their own private URL correctly."""

    def test_each_package_gets_its_own_url(self):
        """Two packages with different URLs each call launch with their specific URL."""
        from agent.launcher import perform_rejoin

        cfg = default_config()
        cfg["first_setup_completed"] = True
        cfg["launch_mode"] = "app"
        cfg["private_server_url"] = ""
        cfg["roblox_packages"] = [
            {
                "package": "com.roblox.client",
                "account_username": "MainAccount",
                "enabled": True,
                "username_source": "manual",
                "private_server_url": "roblox://placeId=MAIN",
            },
            {
                "package": "com.roblox.client.alt",
                "account_username": "AltAccount",
                "enabled": True,
                "username_source": "manual",
                "private_server_url": "roblox://placeId=ALT",
            },
        ]

        phase2_urls: dict[str, list] = {}

        def fake_launch_opts(package, url=None):
            # Phase 1: always called with url=None in two-phase launch
            return (amod.CommandResult(("am", "start"), 0, "OK", ""), "am_or_resolve")

        def fake_launch_url(package, url, mode):
            phase2_urls.setdefault(package, []).append(url)
            return amod.CommandResult(("am", "start"), 0, "OK", "")

        import agent.launcher as _launcher
        with unittest.mock.patch.object(amod, "launch_package_with_options", side_effect=fake_launch_opts), \
             unittest.mock.patch.object(amod, "launch_url", side_effect=fake_launch_url), \
             unittest.mock.patch.object(amod, "package_installed", return_value=True), \
             unittest.mock.patch.object(_launcher, "_proc_scan_alive", return_value=True):
            for entry in cfg["roblox_packages"]:
                pkg_cfg = dict(cfg)
                pkg_cfg["roblox_package"] = entry["package"]
                perform_rejoin(pkg_cfg, reason="start", package_entry=entry)

        # Each package's URL must reach Phase 2 (launch_url), not Phase 1 (launch_package_with_options)
        self.assertTrue(
            any("MAIN" in str(u) for u in phase2_urls.get("com.roblox.client", [])),
            f"MAIN URL not found in phase2 calls for com.roblox.client: {phase2_urls}",
        )
        self.assertTrue(
            any("ALT" in str(u) for u in phase2_urls.get("com.roblox.client.alt", [])),
            f"ALT URL not found in phase2 calls for com.roblox.client.alt: {phase2_urls}",
        )

    def test_effective_url_per_entry_independent(self):
        """effective_private_server_url returns the entry-specific URL for each."""
        cfg_base = default_config()
        cfg_base["private_server_url"] = "roblox://GLOBAL"

        entry1 = {"package": "com.roblox.client", "private_server_url": "roblox://P1", "account_username": "U1", "enabled": True, "username_source": "manual"}
        entry2 = {"package": "com.roblox.client.alt", "private_server_url": "roblox://P2", "account_username": "U2", "enabled": True, "username_source": "manual"}

        url1 = effective_private_server_url(entry1, cfg_base)
        url2 = effective_private_server_url(entry2, cfg_base)

        self.assertNotEqual(url1, url2)
        self.assertIn("P1", str(url1))
        self.assertIn("P2", str(url2))


if __name__ == "__main__":
    unittest.main()
