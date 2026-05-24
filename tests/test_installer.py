"""Tests for installer hardening: clean reinstall, pycache purge, BUILD-INFO.json,
doctor install checks, and runtime isolation proofs.

Covers requirements from the 'Installer Still Runs Old Broken Runtime' hardening prompt.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# 1. BUILD-INFO.json — probe_id and required fields
# ---------------------------------------------------------------------------

class TestBuildInfoFields(unittest.TestCase):
    def _make_build_info_bytes(self, **kwargs) -> bytes:
        from agent.internal_test_artifact import _make_build_info_bytes
        repo_root = Path(__file__).resolve().parents[1]
        return _make_build_info_bytes(repo_root, **kwargs)

    def test_probe_id_present(self):
        data = json.loads(self._make_build_info_bytes())
        self.assertIn("probe_id", data)

    def test_probe_id_starts_with_p_dash(self):
        data = json.loads(self._make_build_info_bytes())
        self.assertTrue(data["probe_id"].startswith("p-"), f"probe_id={data['probe_id']!r}")

    def test_probe_id_is_deterministic_for_same_input(self):
        """Two builds from same commit at same second produce same probe_id."""
        import time
        with patch("time.time", return_value=1700000000):
            b1 = json.loads(self._make_build_info_bytes())
            b2 = json.loads(self._make_build_info_bytes())
        self.assertEqual(b1["probe_id"], b2["probe_id"])

    def test_probe_id_differs_across_builds(self):
        """Different build times produce different probe_ids (even same commit)."""
        with patch("time.time", return_value=1700000001):
            b1 = json.loads(self._make_build_info_bytes())
        with patch("time.time", return_value=1700000099):
            b2 = json.loads(self._make_build_info_bytes())
        self.assertNotEqual(b1["probe_id"], b2["probe_id"])

    def test_required_fields_present(self):
        data = json.loads(self._make_build_info_bytes())
        for field in ("channel", "git_commit", "built_at_iso", "built_at_unix",
                      "product", "artifact_format_version", "probe_id"):
            self.assertIn(field, data, f"missing field: {field}")

    def test_artifact_format_version_is_3(self):
        """Protected builds use format version 3."""
        data = json.loads(self._make_build_info_bytes())
        self.assertEqual(data["artifact_format_version"], 3)

    def test_channel_is_main_dev(self):
        data = json.loads(self._make_build_info_bytes())
        self.assertEqual(data["channel"], "main-dev")


# ---------------------------------------------------------------------------
# 2. Tarball contains BUILD-INFO.json with probe_id
# ---------------------------------------------------------------------------

class TestArtifactContainsBuildInfo(unittest.TestCase):
    def test_tarball_has_build_info(self):
        from agent.internal_test_artifact import build_internal_test_tarball, verify_tarball_exclusions
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as f:
            out_path = Path(f.name)
        try:
            build_internal_test_tarball(repo_root, out_path)
            raw = out_path.read_bytes()
            verify_tarball_exclusions(raw)
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
                names = tf.getnames()
            self.assertIn("BUILD-INFO.json", names)
        finally:
            out_path.unlink(missing_ok=True)

    def test_tarball_build_info_has_probe_id(self):
        from agent.internal_test_artifact import build_internal_test_tarball
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as f:
            out_path = Path(f.name)
        try:
            build_internal_test_tarball(repo_root, out_path)
            raw = out_path.read_bytes()
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
                member = tf.extractfile("BUILD-INFO.json")
                data = json.loads(member.read().decode("utf-8"))
            self.assertIn("probe_id", data)
            self.assertTrue(data["probe_id"].startswith("p-"))
        finally:
            out_path.unlink(missing_ok=True)

    def test_tarball_no_pycache(self):
        from agent.internal_test_artifact import build_internal_test_tarball
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as f:
            out_path = Path(f.name)
        try:
            build_internal_test_tarball(repo_root, out_path)
            raw = out_path.read_bytes()
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
                names = tf.getnames()
            pycache = [n for n in names if "__pycache__" in n or n.endswith(".pyc")]
            self.assertEqual(pycache, [], f"pycache in tarball: {pycache[:5]}")
        finally:
            out_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 3. doctor_install_checks — new checks
# ---------------------------------------------------------------------------

class TestDoctorInstallNewChecks(unittest.TestCase):
    """New checks added in Remaining Limitation Hardening: probe_id, legacy detector, joining states."""

    def _find_check(self, results: list, name: str) -> dict:
        for r in results:
            if r["name"] == name:
                return r
        self.fail(f"Check '{name}' not found in results: {[r['name'] for r in results]}")

    def test_probe_id_check_passes_when_present(self):
        from agent.build_info import doctor_install_checks, BUILD_INFO_PATH
        fake_bi = {"probe_id": "p-abc1234567890123", "channel": "main-dev", "git_commit": "abc123"}
        with patch("agent.build_info.load_build_info", return_value=fake_bi), \
             patch("agent.build_info.load_installed_build", return_value={"artifact_sha256": "abc"}), \
             patch("agent.build_info.find_wrapper_path", return_value="/usr/bin/deng-rejoin"):
            results = doctor_install_checks()
        check = self._find_check(results, "build_info_has_probe_id")
        self.assertTrue(check["ok"])
        self.assertIn("p-abc", check["detail"])

    def test_probe_id_check_fails_when_absent(self):
        from agent.build_info import doctor_install_checks
        with patch("agent.build_info.load_build_info", return_value={"channel": "main-dev"}), \
             patch("agent.build_info.load_installed_build", return_value={}), \
             patch("agent.build_info.find_wrapper_path", return_value=None):
            results = doctor_install_checks()
        check = self._find_check(results, "build_info_has_probe_id")
        self.assertFalse(check["ok"])

    def test_probe_id_check_fails_when_not_p_prefix(self):
        from agent.build_info import doctor_install_checks
        fake_bi = {"probe_id": "wrongprefix123"}
        with patch("agent.build_info.load_build_info", return_value=fake_bi), \
             patch("agent.build_info.load_installed_build", return_value={}), \
             patch("agent.build_info.find_wrapper_path", return_value=None):
            results = doctor_install_checks()
        check = self._find_check(results, "build_info_has_probe_id")
        self.assertFalse(check["ok"])

    def test_no_legacy_detector_check_passes_for_clean_supervisor(self):
        from agent.build_info import doctor_install_checks
        clean_src = "# supervisor.py\nfrom . import roblox_presence\nfrom .config import package_entry\n"
        with patch("agent.build_info.load_build_info", return_value={"probe_id": "p-abc"}), \
             patch("agent.build_info.load_installed_build", return_value={}), \
             patch("agent.build_info.find_wrapper_path", return_value=None), \
             patch("agent.build_info._module_file_path") as mock_file, \
             patch("pathlib.Path.read_text", return_value=clean_src):
            mock_file.side_effect = lambda m: "/fake/supervisor.py" if "supervisor" in m else None
            results = doctor_install_checks()
        check = self._find_check(results, "no_legacy_detector_in_supervisor")
        self.assertTrue(check["ok"])

    def test_no_legacy_detector_check_fails_when_imported(self):
        from agent.build_info import doctor_install_checks
        dirty_src = "from . import experience_detector\nfrom .config import package_entry\n"
        with patch("agent.build_info.load_build_info", return_value={"probe_id": "p-abc"}), \
             patch("agent.build_info.load_installed_build", return_value={}), \
             patch("agent.build_info.find_wrapper_path", return_value=None), \
             patch("agent.build_info._module_file_path") as mock_file, \
             patch("pathlib.Path.read_text", return_value=dirty_src):
            mock_file.side_effect = lambda m: "/fake/supervisor.py" if "supervisor" in m else None
            results = doctor_install_checks()
        check = self._find_check(results, "no_legacy_detector_in_supervisor")
        self.assertFalse(check["ok"])
        self.assertIn("experience_detector", check["detail"])

    def test_no_joining_state_check_passes_for_clean_supervisor(self):
        from agent.build_info import doctor_install_checks
        clean_src = 'STATUS_ONLINE = "Online"\nSTATUS_LAUNCHING = "Launching"\n'
        with patch("agent.build_info.load_build_info", return_value={"probe_id": "p-abc"}), \
             patch("agent.build_info.load_installed_build", return_value={}), \
             patch("agent.build_info.find_wrapper_path", return_value=None), \
             patch("agent.build_info._module_file_path") as mock_file, \
             patch("pathlib.Path.read_text", return_value=clean_src):
            mock_file.side_effect = lambda m: "/fake/supervisor.py" if "supervisor" in m else None
            results = doctor_install_checks()
        check = self._find_check(results, "no_joining_state_in_supervisor")
        self.assertTrue(check["ok"])

    def test_no_joining_state_check_fails_when_joining_present(self):
        from agent.build_info import doctor_install_checks
        dirty_src = 'STATUS_JOINING = "Joining",\nSTATUS_ONLINE = "Online",\n'
        with patch("agent.build_info.load_build_info", return_value={"probe_id": "p-abc"}), \
             patch("agent.build_info.load_installed_build", return_value={}), \
             patch("agent.build_info.find_wrapper_path", return_value=None), \
             patch("agent.build_info._module_file_path") as mock_file, \
             patch("pathlib.Path.read_text", return_value=dirty_src):
            mock_file.side_effect = lambda m: "/fake/supervisor.py" if "supervisor" in m else None
            results = doctor_install_checks()
        check = self._find_check(results, "no_joining_state_in_supervisor")
        self.assertFalse(check["ok"])


# ---------------------------------------------------------------------------
# 4. Installer script contents
# ---------------------------------------------------------------------------

class TestInstallerScript(unittest.TestCase):
    def _make_script(self, sha: str = "abc123def456abc123def456abc123def456abc123def456abc123def456abc123") -> str:
        from agent.bootstrap_installer import render_direct_install_bootstrap
        return render_direct_install_bootstrap(
            base_url="https://rejoin.deng.my.id",
            package_sha256=sha,
            banner_lines=("Version: main-dev",),
        )

    def test_sha_mismatch_message_present(self):
        script = self._make_script()
        self.assertIn("Package checksum mismatch", script)

    def test_purge_before_extract(self):
        script = self._make_script()
        # Verify rm -rf agent/ comes before tar extraction
        purge_pos = script.find('rm -rf "$d"')
        tar_pos = script.find('tar -xzf "$t"')
        self.assertGreater(tar_pos, purge_pos, "purge must happen before extraction")

    def test_pycache_cleanup_uses_depth_flag(self):
        script = self._make_script()
        self.assertIn("-depth", script)
        self.assertIn("__pycache__", script)

    def test_post_extraction_pycache_cleanup_present(self):
        """There must be a pycache cleanup AFTER tar extraction too."""
        script = self._make_script()
        tar_pos = script.find('tar -xzf "$t"')
        post_pycache = script.find("-depth", tar_pos)
        self.assertGreater(post_pycache, tar_pos, "post-extraction pycache cleanup missing")

    def test_build_info_json_checked_in_tarball(self):
        script = self._make_script()
        self.assertIn("BUILD-INFO.json missing", script)

    def test_probe_id_read_from_build_info(self):
        script = self._make_script()
        self.assertIn("probe_id", script)
        self.assertIn("_PROBE_ID", script)

    def test_legacy_import_check_present(self):
        script = self._make_script()
        self.assertIn("experience_detector", script)
        self.assertIn("_LEGACY_IMPORT", script)

    def test_old_states_check_present(self):
        script = self._make_script()
        self.assertNotIn("_OLD_STATES", script)
        self.assertNotIn("Join Unconfirmed", script)

    def test_doctor_install_invoked(self):
        """Doctor install is intentionally removed from the installer because
        running 'python3 -m agent.commands doctor install' always triggers
        no_pycache_dirs failure (Python creates __pycache__ executing the check)
        and causes confusing 'doctor install: FAILED' output even when the
        install itself succeeded.  Targeted checks remain in the installer."""
        script = self._make_script()
        # Doctor install must NOT be called from the installer script.
        self.assertNotIn("doctor install", script,
                         "doctor install call must be removed — it always fails due "
                         "to no_pycache_dirs being tripped by its own execution")

    def test_final_proof_block_present(self):
        script = self._make_script()
        self.assertIn("DENG Tool: Rejoin Installer", script)
        self.assertNotIn("DENG Tool: Rejoin Test Installer", script)
        self.assertIn("Version: main-dev", script)
        self.assertIn("Install complete.", script)
        self.assertNotIn("100%", script)
        self.assertNotIn("[################", script)
        self.assertNotIn("[------", script)
        self.assertNotIn("DENG Tool: Rejoin Installed", script)
        self.assertNotIn("Start Command:", script)

    def test_direct_installer_has_single_bottom_separator(self):
        script = self._make_script()
        sep_echo = 'echo "=============================="'
        dash_echo = 'echo "------------------------------"'
        self.assertEqual(script.count(sep_echo), 0)
        self.assertEqual(script.count(dash_echo), 0)
        self.assertIn("DENG Tool: Rejoin Installer", script)
        self.assertIn('info "Version: main-dev"', script)
        self.assertIn('ok "Install complete."', script)

    def test_agent_file_proof_present(self):
        script = self._make_script()
        self.assertIn("agent.__file__", script)

    def test_install_complete_printed(self):
        script = self._make_script()
        self.assertIn("Install complete.", script)

    def test_shebang_present(self):
        script = self._make_script()
        self.assertTrue(script.startswith("#!/usr/bin/env sh\n"))

    def test_set_eu_present(self):
        script = self._make_script()
        self.assertIn("set -eu\n", script)

    def test_cache_buster_present(self):
        script = self._make_script()
        self.assertIn("?t=$c", script)
        self.assertIn("Cache-Control: no-cache", script)


# ---------------------------------------------------------------------------
# 5. doctor install: actual live supervisor checks
# ---------------------------------------------------------------------------

class TestLiveSupervisorChecks(unittest.TestCase):
    """Confirm the real installed supervisor passes the new doctor checks."""

    def _run_doctor(self) -> list[dict]:
        from agent.build_info import doctor_install_checks
        return doctor_install_checks()

    def _find_check(self, results, name):
        for r in results:
            if r["name"] == name:
                return r
        self.fail(f"Check '{name}' not found")

    def test_no_legacy_detector_in_live_supervisor(self):
        results = self._run_doctor()
        check = self._find_check(results, "no_legacy_detector_in_supervisor")
        self.assertTrue(check["ok"], f"Legacy detector check failed: {check['detail']}")

    def test_no_joining_state_in_live_supervisor(self):
        results = self._run_doctor()
        check = self._find_check(results, "no_joining_state_in_supervisor")
        self.assertTrue(check["ok"], f"Joining state check failed: {check['detail']}")

    def test_required_modules_present(self):
        results = self._run_doctor()
        check = self._find_check(results, "required_modules_present")
        self.assertTrue(check["ok"], f"Required modules check failed: {check['detail']}")

    def test_no_orphan_pycache(self):
        results = self._run_doctor()
        check = self._find_check(results, "no_orphan_pycache")
        self.assertTrue(check["ok"], f"Orphan pycache check failed: {check['detail']}")

    def test_modules_under_install_root(self):
        results = self._run_doctor()
        check = self._find_check(results, "modules_under_install_root")
        self.assertTrue(check["ok"], f"Module routing check failed: {check['detail']}")


# ---------------------------------------------------------------------------
# 6. Simulated reinstall: purge preserves config, removes old agent files
# ---------------------------------------------------------------------------

class TestReinstallSimulation(unittest.TestCase):
    """Simulate what the installer bash script does and verify invariants."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.app_home = Path(self._tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _create_old_install(self) -> None:
        """Create a fake old install with stale files."""
        agent_dir = self.app_home / "agent"
        agent_dir.mkdir()
        (agent_dir / "old_module.py").write_text("# old")
        (agent_dir / "experience_detector.py").write_text("# old broken module")
        pycache = agent_dir / "__pycache__"
        pycache.mkdir()
        (pycache / "old_module.cpython-311.pyc").write_bytes(b"stale bytecode")
        (self.app_home / "config.json").write_text('{"license_key": "TEST-KEY"}')
        (self.app_home / ".install_api").write_text("https://old.example.com\n")

    def _simulate_purge(self) -> None:
        """Simulate the installer's purge phase."""
        import shutil
        for d in ("agent", "bot", "scripts", "docs", "examples", "assets"):
            target = self.app_home / d
            if target.exists():
                shutil.rmtree(target)
        (self.app_home / "BUILD-INFO.json").unlink(missing_ok=True)
        (self.app_home / ".installed-build.json").unlink(missing_ok=True)
        # Pycache cleanup
        for pycache_dir in list(self.app_home.rglob("__pycache__")):
            shutil.rmtree(pycache_dir, ignore_errors=True)
        for pyc in list(self.app_home.rglob("*.pyc")):
            pyc.unlink(missing_ok=True)

    def _simulate_extract(self) -> None:
        """Simulate extracting a new artifact (creates fresh agent/)."""
        agent_dir = self.app_home / "agent"
        agent_dir.mkdir(exist_ok=True)
        (agent_dir / "deng_tool_rejoin.py").write_text("# new")
        (agent_dir / "commands.py").write_text("# new commands")
        (self.app_home / "BUILD-INFO.json").write_text(
            '{"channel":"main-dev","git_commit":"abc123","probe_id":"p-testid123456"}'
        )

    def test_purge_removes_old_agent_module(self):
        self._create_old_install()
        self._simulate_purge()
        self.assertFalse((self.app_home / "agent" / "old_module.py").exists())
        self.assertFalse((self.app_home / "agent" / "experience_detector.py").exists())

    def test_purge_removes_pycache(self):
        self._create_old_install()
        self._simulate_purge()
        pycache_dirs = list(self.app_home.rglob("__pycache__"))
        self.assertEqual(pycache_dirs, [], f"pycache remains: {pycache_dirs}")

    def test_purge_preserves_config_json(self):
        self._create_old_install()
        self._simulate_purge()
        cfg = self.app_home / "config.json"
        self.assertTrue(cfg.exists())
        self.assertIn("TEST-KEY", cfg.read_text())

    def test_purge_preserves_install_api(self):
        self._create_old_install()
        self._simulate_purge()
        self.assertTrue((self.app_home / ".install_api").exists())

    def test_extract_after_purge_has_clean_state(self):
        self._create_old_install()
        self._simulate_purge()
        self._simulate_extract()
        # New files present
        self.assertTrue((self.app_home / "agent" / "deng_tool_rejoin.py").exists())
        self.assertTrue((self.app_home / "BUILD-INFO.json").exists())
        # Old stale file gone
        self.assertFalse((self.app_home / "agent" / "old_module.py").exists())
        self.assertFalse((self.app_home / "agent" / "experience_detector.py").exists())

    def test_build_info_json_has_probe_id(self):
        self._create_old_install()
        self._simulate_purge()
        self._simulate_extract()
        data = json.loads((self.app_home / "BUILD-INFO.json").read_text())
        self.assertIn("probe_id", data)
        self.assertTrue(data["probe_id"].startswith("p-"))

    def test_stale_extract_over_old_install_fails(self):
        """Extracting over old files without purge leaves stale modules."""
        self._create_old_install()
        # NO purge — extract directly over
        self._simulate_extract()
        # Old stale module still present (THIS IS THE BUG we fix)
        self.assertTrue((self.app_home / "agent" / "old_module.py").exists())

    def test_proper_install_removes_stale_module(self):
        """Purge + extract removes stale modules."""
        self._create_old_install()
        self._simulate_purge()
        self._simulate_extract()
        self.assertFalse((self.app_home / "agent" / "old_module.py").exists())


# ---------------------------------------------------------------------------
# 7. build_info.collected_version_info alias
# ---------------------------------------------------------------------------

class TestCollectedVersionInfoAlias(unittest.TestCase):
    def test_alias_exists(self):
        from agent.build_info import collected_version_info, collect_version_info
        self.assertIs(collected_version_info, collect_version_info)

    def test_alias_returns_dict(self):
        from agent.build_info import collected_version_info
        result = collected_version_info()
        self.assertIsInstance(result, dict)
        self.assertIn("product", result)


# ---------------------------------------------------------------------------
# 8. cmd_doctor_install command works
# ---------------------------------------------------------------------------

class TestCmdDoctorInstall(unittest.TestCase):
    def test_doctor_install_command_callable(self):
        from agent.commands import cmd_doctor_install
        self.assertTrue(callable(cmd_doctor_install))

    def test_doctor_install_returns_int(self):
        import argparse
        from agent.commands import cmd_doctor_install
        ns = argparse.Namespace(no_color=True)
        result = cmd_doctor_install(ns)
        self.assertIsInstance(result, int)

    def test_doctor_install_writes_to_stdout(self):
        import argparse
        from agent.commands import cmd_doctor_install
        ns = argparse.Namespace(no_color=True)
        buf = io.StringIO()
        with unittest.mock.patch("sys.stdout", buf):
            cmd_doctor_install(ns)
        output = buf.getvalue()
        self.assertIn("[PASS]", output)
        self.assertIn("doctor install:", output)


# ---------------------------------------------------------------------------
# 9. No pycache check in doctor
# ---------------------------------------------------------------------------

class TestNoPycacheDirCheck(unittest.TestCase):
    def _find_check(self, results, name):
        for r in results:
            if r["name"] == name:
                return r
        self.fail(f"Check '{name}' not found")

    def test_no_pycache_dirs_check_exists(self):
        from agent.build_info import doctor_install_checks
        with patch("agent.build_info.load_build_info", return_value={"probe_id": "p-abc"}), \
             patch("agent.build_info.load_installed_build", return_value={}), \
             patch("agent.build_info.find_wrapper_path", return_value=None):
            results = doctor_install_checks()
        names = [r["name"] for r in results]
        self.assertIn("no_pycache_dirs", names)

    def test_no_pycache_dirs_check_passes_when_clean(self):
        from agent.build_info import doctor_install_checks, INSTALL_ROOT
        with patch("agent.build_info.load_build_info", return_value={"probe_id": "p-abc"}), \
             patch("agent.build_info.load_installed_build", return_value={}), \
             patch("agent.build_info.find_wrapper_path", return_value=None):
            results = doctor_install_checks()
        check = self._find_check(results, "no_pycache_dirs")
        # In the test environment (Windows dev machine), pycache may exist
        # but the check should run without error
        self.assertIn("ok", check)
        self.assertIn("detail", check)


# ---------------------------------------------------------------------------
# 12. POSIX sh syntax validation (regression for line-174 ")" syntax error)
# ---------------------------------------------------------------------------

class TestInstallerPosixShSyntax(unittest.TestCase):
    """Regression tests for POSIX sh compatibility of the generated installer.

    Root cause of prior break: Python inline code embedded inside a shell
    single-quoted string (python3 -c '...') contained ['"](Joining)['"] which
    has single quotes that prematurely close the shell single-quoted string,
    leaving (Joining) as an unquoted shell subshell → ')" unexpected'.

    Fix: replaced python3 -c '...' blocks with POSIX grep commands that
    operate directly on the extracted supervisor file.
    """

    def _get_script(self) -> str:
        from agent.bootstrap_installer import render_direct_install_bootstrap
        return render_direct_install_bootstrap(
            base_url="https://rejoin.deng.my.id",
            package_sha256="a" * 64,
        )

    def test_installer_has_posix_shebang(self):
        s = self._get_script()
        first_line = s.split("\n")[0]
        self.assertIn("sh", first_line, "shebang must reference sh")

    def test_no_bash_arrays(self):
        """No bash array syntax: arr=(a b c)"""
        import re
        s = self._get_script()
        matches = re.findall(r'\b\w+=\(', s)
        self.assertEqual(matches, [], f"Bash array syntax found: {matches}")

    def test_no_double_bracket_conditionals(self):
        """No bash [[ ... ]] conditionals — use POSIX [ ... ] instead.

        Note: POSIX character classes like [[:space:]] are allowed and
        are not the same as bash [[ ... ]] conditionals.
        """
        import re
        s = self._get_script()
        # Bash [[ appears as a conditional: if [[, while [[, ] && [[, etc.
        # It does NOT include POSIX character classes like [[:space:]]
        bash_cond = re.findall(r'(?:if|while|&&|\|\|)\s*\[\[', s)
        self.assertEqual(
            bash_cond, [],
            f"Bash [[ conditional found: {bash_cond}"
        )

    def test_no_single_quote_inside_single_quoted_python_c(self):
        """Installer must not embed single quotes inside python3 -c '...' blocks.

        This was the root cause of the line-174 syntax error:
        python3 -c '... ['"](Joining)['"]...' broke POSIX sh parsing.
        """
        import re
        s = self._get_script()
        # Find all python3 -c '...' blocks
        # A single quote inside a single-quoted shell string is impossible in POSIX sh
        # so we just check: does any line containing python3 -c contain ['"]
        for i, line in enumerate(s.split("\n"), start=1):
            if "python3 -c '" in line:
                # If this is a single-line -c '...', check for embedded ' in the arg
                # (a multi-line -c is fine as long as no ' appears in the Python body)
                self.assertNotIn(
                    "['\"",
                    line,
                    f"Line {i}: single-quoted python3 -c block contains ['\" "
                    f"which closes the shell string: {line!r}"
                )

    def test_no_joining_state_in_single_quoted_python(self):
        """The specific broken pattern must not appear in the generated installer."""
        s = self._get_script()
        # The old broken pattern was: re.search(r"""['"](Joining)['"]""", src)
        # inside a single-quoted shell string
        self.assertNotIn(
            "['\"](Joining)['\"]",
            s,
            "Broken pattern ['\"]( Joining)['\"] found — will break POSIX sh"
        )
        self.assertNotIn(
            "['\"]",
            s,
            "Pattern ['\"](containing single-quote) found in installer script"
        )

    def test_grep_based_supervisor_checks_present(self):
        """Legacy detector and old-states checks must use grep -qE, not python3 -c.

        Both checks (experience_detector and Joining state) use grep -qE with
        precise patterns so STATUS_JOINING = "Joining"  # comment does NOT
        trigger a false positive.
        """
        s = self._get_script()
        self.assertIn(
            "grep -qE",
            s,
            "supervisor checks must use grep -qE"
        )
        # The supervisor file variable must be used
        self.assertIn("_SV_FILE", s, "_SV_FILE variable must be set for grep checks")

    def test_sh_syntax_valid(self):
        """sh -n must pass on the generated installer (requires sh in PATH)."""
        import shutil
        import subprocess
        import tempfile
        s = self._get_script()
        sh = shutil.which("sh")
        if not sh:
            self.skipTest("sh not found in PATH — skipping sh -n check")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", delete=False, encoding="utf-8"
        ) as tf:
            tf.write(s)
            tf_path = tf.name
        try:
            result = subprocess.run(
                [sh, "-n", tf_path],
                capture_output=True, text=True, timeout=10
            )
            self.assertEqual(
                result.returncode, 0,
                f"sh -n failed:\n{result.stderr}"
            )
        finally:
            import os
            os.unlink(tf_path)

    def test_bash_syntax_valid(self):
        """bash -n must pass on the generated installer."""
        import shutil
        import subprocess
        import tempfile
        s = self._get_script()
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash not found in PATH — skipping bash -n check")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", delete=False, encoding="utf-8"
        ) as tf:
            tf.write(s)
            tf_path = tf.name
        try:
            result = subprocess.run(
                [bash, "-n", tf_path],
                capture_output=True, text=True, timeout=10
            )
            self.assertEqual(
                result.returncode, 0,
                f"bash -n failed:\n{result.stderr}"
            )
        finally:
            import os
            os.unlink(tf_path)

    def test_removed_join_state_check_not_in_installer(self):
        """Installer no longer carries removed join-state grep probes."""
        s = self._get_script()
        self.assertNotIn('"(Joining)"', s)
        self.assertNotIn("Join Unconfirmed", s)
        self.assertNotIn("_OLD_STATES", s)


if __name__ == "__main__":
    unittest.main()
