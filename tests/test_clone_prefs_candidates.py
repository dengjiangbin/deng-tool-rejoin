"""Multi-format clone-wrapper prefs detection.

Discovered from real probe ``p-368a65d699`` (Samsung SM-N9810, Android 13):

* Moons multi-clone packages (``com.moons.litesc/d/e``) DO NOT have
  ``pkg_preferences.xml``.  They have ``<package>_preferences.xml`` and
  ``prefs.xml``.
* The current writer only checks ``pkg_preferences.xml`` and gives up
  with ``"pkg_preferences.xml not found (no App Cloner clone?)"``.

These tests pin the new behaviour:

* ``clone_prefs_candidates`` returns multiple file paths.
* ``update_app_cloner_xml`` iterates them in order and writes to the
  first match.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import window_layout as wl  # noqa: E402


class CandidatesTests(unittest.TestCase):
    def test_returns_pkg_preferences_first(self) -> None:
        c = wl.clone_prefs_candidates("com.moons.litesc")
        self.assertEqual(c[0].name, "pkg_preferences.xml")

    def test_includes_package_specific_name(self) -> None:
        c = wl.clone_prefs_candidates("com.moons.litesc")
        names = [p.name for p in c]
        self.assertIn("com.moons.litesc_preferences.xml", names)

    def test_includes_prefs_xml_and_settings(self) -> None:
        c = wl.clone_prefs_candidates("com.roblox.client")
        names = [p.name for p in c]
        for expected in ("pkg_preferences.xml", "prefs.xml", "cloner_settings.xml", "settings.xml"):
            self.assertIn(expected, names)

    def test_all_under_correct_data_directory(self) -> None:
        for p in wl.clone_prefs_candidates("com.example.app"):
            self.assertEqual(
                str(p.parent),
                "/data/data/com.example.app/shared_prefs"
                if os.sep == "/" else
                str(Path("/data/data") / "com.example.app" / "shared_prefs"),
            )


class DirectWriterIteratesCandidatesTests(unittest.TestCase):
    """``update_app_cloner_xml`` walks every candidate; first hit wins."""

    def test_writes_to_moons_package_preferences_when_pkg_missing(self) -> None:
        package = "com.moons.litesc"
        # Build a fake "existing" file mapping that mirrors what the
        # real Moons clone has: no pkg_preferences.xml, but a per-package
        # _preferences.xml that contains a valid <map/>.
        moons_xml = '<?xml version="1.0" encoding="utf-8" standalone="yes" ?>\n<map />'
        fake_fs: dict[str, str] = {
            f"/data/data/{package}/shared_prefs/{package}_preferences.xml": moons_xml,
        }
        writes: dict[str, str] = {}

        def fake_exists(self: Path) -> bool:  # noqa: ARG001
            return str(self).replace(os.sep, "/") in fake_fs

        def fake_read_text(self: Path, encoding: str = "utf-8") -> str:  # noqa: ARG001
            return fake_fs[str(self).replace(os.sep, "/")]

        def fake_write_text(self: Path, content: str, encoding: str = "utf-8") -> int:  # noqa: ARG001
            writes[str(self).replace(os.sep, "/")] = content
            return len(content)

        def fake_parse(path):
            from xml.etree import ElementTree as ET
            return ET.ElementTree(ET.fromstring(fake_fs[str(path).replace(os.sep, "/")]))

        from agent.window_layout import WindowRect
        rect = WindowRect(
            package=package, left=100, top=100, right=900, bottom=500,
        )

        with patch.object(Path, "exists", fake_exists), \
             patch.object(Path, "read_text", fake_read_text), \
             patch.object(Path, "write_text", fake_write_text), \
             patch("agent.window_layout.ET.parse", side_effect=fake_parse), \
             patch("agent.window_layout.shutil.copy2"):
            ok, msg = wl.update_app_cloner_xml(package, rect)
        self.assertTrue(ok, msg=msg)
        # The Moons-specific file got the write, not the App-Cloner one.
        self.assertIn(
            f"/data/data/{package}/shared_prefs/{package}_preferences.xml",
            writes,
        )

    def test_reports_attempted_candidates_when_none_exist(self) -> None:
        package = "com.no.such.app"
        from agent.window_layout import WindowRect
        rect = WindowRect(
            package=package, left=0, top=0, right=100, bottom=100,
        )
        with patch.object(Path, "exists", lambda self: False):
            ok, msg = wl.update_app_cloner_xml(package, rect)
        self.assertFalse(ok)
        self.assertIn("missing", msg)
        # The message should mention at least one specific candidate filename.
        self.assertTrue(
            any(n in msg for n in (
                "pkg_preferences.xml", f"{package}_preferences.xml", "prefs.xml",
            )),
            msg=f"unexpected message: {msg}",
        )


if __name__ == "__main__":
    unittest.main()
