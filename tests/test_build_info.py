"""Runtime build-proof helpers — agent.build_info."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import build_info


class CollectVersionInfoTests(unittest.TestCase):
    def test_emits_required_keys(self) -> None:
        info = build_info.collect_version_info()
        # These keys must always appear even when nothing is on disk.
        for key in (
            "product",
            "product_version",
            "channel",
            "git_commit_short",
            "artifact_sha256_short",
            "install_root",
            "python_executable",
            "python_version",
            "modules",
        ):
            self.assertIn(key, info, msg=f"missing key {key}")
        # Required modules must always be enumerated, even if any are missing.
        for mod in build_info.REQUIRED_MODULES:
            self.assertIn(mod, info["modules"])

    def test_reads_build_info_json(self) -> None:
        # Drop a temporary BUILD-INFO.json next to ``agent/`` and verify.
        bi_path = build_info.BUILD_INFO_PATH
        backup = bi_path.read_bytes() if bi_path.is_file() else None
        try:
            bi_path.write_text(
                json.dumps(
                    {
                        "channel": "main-dev",
                        "git_commit": "abcdef0123456789",
                        "built_at_iso": "2025-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            info = build_info.collect_version_info()
            self.assertEqual(info["channel"], "main-dev")
            self.assertEqual(info["git_commit"], "abcdef0123456789")
            self.assertEqual(info["git_commit_short"], "abcdef012345")
            self.assertEqual(info["built_at_iso"], "2025-01-01T00:00:00Z")
        finally:
            if backup is not None:
                bi_path.write_bytes(backup)
            else:
                bi_path.unlink(missing_ok=True)

    def test_installed_build_overrides_build_info(self) -> None:
        # .installed-build.json takes precedence (installer-verified SHA).
        bi_path = build_info.BUILD_INFO_PATH
        ib_path = build_info.INSTALLED_BUILD_PATH
        bi_backup = bi_path.read_bytes() if bi_path.is_file() else None
        ib_backup = ib_path.read_bytes() if ib_path.is_file() else None
        try:
            bi_path.write_text(
                json.dumps({"git_commit": "from-buildinfo"}), encoding="utf-8"
            )
            ib_path.write_text(
                json.dumps(
                    {
                        "git_commit": "from-installer",
                        "artifact_sha256": "f" * 64,
                        "channel": "main-dev",
                        "install_time_iso": "2025-05-01T00:00:00Z",
                        "install_api": "https://example.test",
                        "package_url": "https://example.test/install/test/package.tar.gz",
                        "installer_url": "https://example.test/install/test/latest",
                    }
                ),
                encoding="utf-8",
            )
            info = build_info.collect_version_info()
            # Installer commit wins.
            self.assertEqual(info["git_commit"], "from-installer")
            self.assertEqual(info["artifact_sha256_short"], "f" * 12)
            self.assertEqual(info["install_api"], "https://example.test")
        finally:
            for path, backup in [(bi_path, bi_backup), (ib_path, ib_backup)]:
                if backup is not None:
                    path.write_bytes(backup)
                else:
                    path.unlink(missing_ok=True)


class WrapperPathParsingTests(unittest.TestCase):
    def test_parses_app_home_style(self) -> None:
        # Drop a fake wrapper into a temp PATH dir and probe.
        d = Path(tempfile.mkdtemp())
        try:
            wrapper = d / "deng-rejoin"
            wrapper.write_text(
                "#!/bin/sh\n"
                'APP_HOME="$HOME/.deng-tool/rejoin"\n'
                "exec true\n",
                encoding="utf-8",
            )
            with patch("agent.build_info.find_wrapper_path", return_value=str(wrapper)):
                root = build_info._wrapper_target_install_root()
            self.assertEqual(root, "$HOME/.deng-tool/rejoin")
        finally:
            for f in d.iterdir():
                f.unlink()
            d.rmdir()

    def test_parses_deng_rejoin_home_with_default(self) -> None:
        d = Path(tempfile.mkdtemp())
        try:
            wrapper = d / "deng-rejoin"
            wrapper.write_text(
                "#!/bin/sh\n"
                'export DENG_REJOIN_HOME="${DENG_REJOIN_HOME:-$HOME/.deng-tool/rejoin}"\n'
                "exec true\n",
                encoding="utf-8",
            )
            with patch("agent.build_info.find_wrapper_path", return_value=str(wrapper)):
                root = build_info._wrapper_target_install_root()
            self.assertEqual(root, "$HOME/.deng-tool/rejoin")
        finally:
            for f in d.iterdir():
                f.unlink()
            d.rmdir()

    def test_no_wrapper_returns_none(self) -> None:
        with patch("agent.build_info.find_wrapper_path", return_value=None):
            self.assertIsNone(build_info._wrapper_target_install_root())


class DoctorInstallChecksTests(unittest.TestCase):
    def test_runs_all_named_checks_in_order(self) -> None:
        results = build_info.doctor_install_checks()
        names = [r["name"] for r in results]
        for required in (
            "build_info_present",
            "installed_build_metadata",
            "artifact_sha_recorded",
            "required_modules_present",
            "required_symbols_resolvable",
            "no_orphan_pycache",
            "no_stale_deferred_installer",
            "modules_under_install_root",
            "wrapper_present",
        ):
            self.assertIn(required, names, msg=f"missing check {required}")

    def test_required_symbols_resolve_in_this_checkout(self) -> None:
        results = build_info.doctor_install_checks()
        sym_check = next(r for r in results if r["name"] == "required_symbols_resolvable")
        self.assertTrue(
            sym_check["ok"],
            msg=f"required symbols failed: {sym_check['detail']}",
        )

    def test_required_modules_resolve_in_this_checkout(self) -> None:
        results = build_info.doctor_install_checks()
        mod_check = next(r for r in results if r["name"] == "required_modules_present")
        self.assertTrue(
            mod_check["ok"],
            msg=f"required modules failed: {mod_check['detail']}",
        )

    def test_doctor_install_overall_ok_aggregator(self) -> None:
        self.assertTrue(
            build_info.doctor_install_overall_ok(
                [{"ok": True, "name": "a"}, {"ok": True, "name": "b"}]
            )
        )
        self.assertFalse(
            build_info.doctor_install_overall_ok(
                [{"ok": True, "name": "a"}, {"ok": False, "name": "b"}]
            )
        )
        self.assertFalse(build_info.doctor_install_overall_ok([]) is False)
        # empty list aggregates to True (vacuous); we explicitly want all()
        self.assertTrue(build_info.doctor_install_overall_ok([]))


if __name__ == "__main__":
    unittest.main()
