"""Tests for the App Cloner / cloud-phone layout key discovery."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import layout_discovery as ld


_SAMPLE_XML = """<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <int name="app_cloner_current_window_left" value="100" />
    <int name="app_cloner_current_window_top" value="50" />
    <int name="app_cloner_window_position_landscape_left" value="200" />
    <int name="app_cloner_window_size_landscape_width" value="800" />
    <boolean name="app_cloner_set_window_position" value="true" />
    <boolean name="app_cloner_set_window_size" value="false" />
    <boolean name="app_cloner_auto_dpi_landscape" value="false" />
    <boolean name="app_cloner_force_landscape" value="false" />
    <int name="app_cloner_target_dpi" value="320" />
    <string name="unrelated_setting">ignored</string>
    <int name="counter">42</int>
</map>
"""

_INVALID_XML = "<not really xml>"


class TestClassify(unittest.TestCase):
    """_classify must categorise each key against the pattern table."""

    def test_position_left_categorised(self):
        cats = ld._classify("app_cloner_current_window_left")
        self.assertIn("position_left", cats)

    def test_auto_dpi_landscape_categorised(self):
        cats = ld._classify("app_cloner_auto_dpi_landscape")
        self.assertIn("auto_dpi", cats)
        self.assertIn("orient_landscape", cats)

    def test_set_window_position_categorised(self):
        cats = ld._classify("app_cloner_set_window_position")
        self.assertIn("set_position_enable", cats)

    def test_set_window_size_categorised(self):
        cats = ld._classify("app_cloner_set_window_size")
        self.assertIn("set_size_enable", cats)

    def test_force_landscape_categorised(self):
        cats = ld._classify("app_cloner_force_landscape")
        self.assertIn("orient_landscape", cats)

    def test_size_height_categorised(self):
        cats = ld._classify("app_cloner_window_size_landscape_height")
        self.assertIn("size_height", cats)
        self.assertIn("orient_landscape", cats)

    def test_unrelated_key_has_no_category(self):
        self.assertEqual(ld._classify("counter"), ())
        self.assertEqual(ld._classify("unrelated_setting"), ())


class TestParseXmlKeys(unittest.TestCase):
    """_parse_xml_keys must categorise every relevant entry."""

    def test_extracts_known_keys(self):
        keys = ld._parse_xml_keys(_SAMPLE_XML, "test.xml", writable_direct=True)
        names = {k.name for k in keys}
        self.assertIn("app_cloner_current_window_left", names)
        self.assertIn("app_cloner_set_window_position", names)
        self.assertIn("app_cloner_auto_dpi_landscape", names)
        # Unrelated keys are not included
        self.assertNotIn("counter", names)
        self.assertNotIn("unrelated_setting", names)

    def test_invalid_xml_returns_empty(self):
        self.assertEqual(
            ld._parse_xml_keys(_INVALID_XML, "test.xml", writable_direct=True),
            [],
        )

    def test_empty_xml_returns_empty(self):
        self.assertEqual(ld._parse_xml_keys("", "test.xml", writable_direct=True), [])

    def test_writable_flag_propagates(self):
        keys = ld._parse_xml_keys(_SAMPLE_XML, "x.xml", writable_direct=False)
        self.assertTrue(keys, "expected at least one key")
        for k in keys:
            self.assertFalse(k.writable_direct)


class TestPackageDiscoveryAggregations(unittest.TestCase):
    """PackageDiscovery exposes by_category / has_category / summary."""

    def setUp(self):
        self.disc = ld.PackageDiscovery(package="com.x")
        self.disc.keys = ld._parse_xml_keys(_SAMPLE_XML, "/data/data/com.x/shared_prefs/pkg_preferences.xml", writable_direct=True)

    def test_has_set_position_enable(self):
        self.assertTrue(self.disc.has_category("set_position_enable"))

    def test_by_category_finds_landscape_keys(self):
        cands = self.disc.by_category("orient_landscape")
        self.assertGreater(len(cands), 0)

    def test_summary_returns_counts(self):
        s = self.disc.summary()
        self.assertGreater(s.get("orient_landscape", 0), 0)
        self.assertGreater(s.get("set_position_enable", 0), 0)


class TestDiscoveryHandlesMissingPackage(unittest.TestCase):
    """Discovery must NEVER raise even for unknown packages or root errors."""

    def test_missing_package_returns_empty_discovery(self):
        # No shared_prefs dir; direct list yields []; root unavailable.
        disc = ld.discover_for_package("com.does.not.exist", root_tool=None)
        self.assertIsInstance(disc, ld.PackageDiscovery)
        self.assertEqual(disc.keys, [])

    def test_root_command_error_does_not_raise(self):
        from agent import android
        with patch.object(android, "run_root_command",
                          side_effect=RuntimeError("boom")):
            disc = ld.discover_for_package("com.foo", root_tool="su")
        self.assertIsInstance(disc, ld.PackageDiscovery)
        # Either no keys or partial — but no exception
        self.assertIsNotNone(disc)


class TestDiscoveryLogWriter(unittest.TestCase):
    """write_discovery_log creates a readable file at the expected path."""

    def test_writes_file_with_package_block(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "layout-discovery.log"
            disc = ld.PackageDiscovery(package="com.x")
            disc.keys = ld._parse_xml_keys(_SAMPLE_XML, "x.xml", writable_direct=True)
            disc.files_scanned = ["x.xml"]
            written = ld.write_discovery_log({"com.x": disc}, path=target)
            self.assertEqual(written, target)
            self.assertTrue(target.exists())
            body = target.read_text(encoding="utf-8")
            self.assertIn("Package: com.x", body)
            self.assertIn("app_cloner_set_window_position", body)
            self.assertIn("category_counts", body)

    def test_never_prints_to_stdout(self):
        out = io.StringIO()
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "layout-discovery.log"
            disc = ld.PackageDiscovery(package="com.x")
            with redirect_stdout(out), redirect_stderr(err):
                ld.write_discovery_log({"com.x": disc}, path=target)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")


class TestRunDiscoveryAndLogIntegration(unittest.TestCase):
    """run_discovery_and_log returns path and discoveries."""

    def test_calls_discover_all_and_writes_file(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "layout-discovery.log"
            fake_disc = ld.PackageDiscovery(package="com.x")
            fake_disc.keys = ld._parse_xml_keys(_SAMPLE_XML, "x.xml", writable_direct=True)
            fake_disc.files_scanned = ["x.xml"]
            with patch.object(ld, "discover_all",
                              return_value={"com.x": fake_disc}):
                ld.clear_cache()
                path, discs = ld.run_discovery_and_log(
                    ["com.x"], root_tool=None, refresh=True, path=target,
                )
            self.assertEqual(path, target)
            self.assertIn("com.x", discs)
            self.assertTrue(target.exists())


class TestCacheBehaviour(unittest.TestCase):
    def setUp(self):
        ld.clear_cache()

    def test_get_cached_or_discover_uses_cache(self):
        calls: list[str] = []

        def fake_discover_all(packages, *, root_tool=None):
            calls.append("called")
            return {pkg: ld.PackageDiscovery(package=pkg) for pkg in packages}

        with patch.object(ld, "discover_all", side_effect=fake_discover_all):
            ld.get_cached_or_discover(["com.a"], root_tool=None, refresh=True)
            self.assertEqual(len(calls), 1)
            # Second call within TTL: should NOT re-discover.
            ld.get_cached_or_discover(["com.a"], root_tool=None, refresh=False)
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
