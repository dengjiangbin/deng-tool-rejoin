"""Installer bash script (test/latest) — clean-install behavior.

These tests do not run bash.  They inspect the rendered script text for the
critical behaviours required by TASK 2 in the install/runtime verification
prompt: cache-busting, SHA verify, purge old code, write .installed-build.json,
import-check, post-install version-check.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.bootstrap_installer import render_direct_install_bootstrap


def _script(sha: str = "a" * 64) -> str:
    return render_direct_install_bootstrap(
        base_url="https://rejoin.deng.my.id",
        package_sha256=sha,
    )


class CacheBustingTests(unittest.TestCase):
    def test_curl_uses_cache_bust_query_param(self) -> None:
        s = _script()
        # We pin the URL once, then download with ?t=cache-buster suffix.
        self.assertIn("CACHE_BUSTER=", s)
        self.assertIn("?t=$CACHE_BUSTER", s)
        # Also send the canonical no-cache request headers.
        self.assertIn('-H "Cache-Control: no-cache"', s)
        self.assertIn('-H "Pragma: no-cache"', s)

    def test_keeps_cloudflare_safe_ua(self) -> None:
        # The UA bypasses Cloudflare's Browser Integrity Check; keep it.
        self.assertIn("deng-rejoin-installer/1.0", _script())


class PurgeStepTests(unittest.TestCase):
    def test_purges_known_code_directories(self) -> None:
        s = _script()
        # The purge loop is the sole guarantee against orphan files from the
        # previous build.  Every code directory must be named.
        self.assertIn("for _d in agent bot scripts docs examples assets", s)
        self.assertIn('rm -rf "$APP_HOME/$_d"', s)

    def test_purges_pycache_recursively(self) -> None:
        s = _script()
        self.assertIn('find "$APP_HOME" -type d -name __pycache__', s)
        self.assertIn('find "$APP_HOME" -type f -name "*.pyc"', s)

    def test_removes_previous_build_metadata(self) -> None:
        s = _script()
        self.assertIn('rm -f "$APP_HOME/BUILD-INFO.json"', s)
        self.assertIn('"$APP_HOME/.installed-build.json"', s)

    def test_stops_running_processes_before_install(self) -> None:
        s = _script()
        # Best-effort pkill against the wrapper process.
        self.assertIn("pkill -f 'agent/deng_tool_rejoin.py'", s)
        # Honour pid file if present.
        self.assertIn("$APP_HOME/data/rejoin.pid", s)


class ShaVerifyTests(unittest.TestCase):
    def test_aborts_on_sha_mismatch(self) -> None:
        s = _script()
        self.assertIn("EXPECTED_SHA256=", s)
        self.assertIn("ACTUAL_SHA", s)
        self.assertIn("hashlib.sha256", s)
        self.assertIn("Package checksum mismatch", s)
        # SHA check must come before extraction.
        sha_idx = s.index("Package checksum mismatch")
        extract_idx = s.index('tar -xzf "$TMP"')
        self.assertLess(sha_idx, extract_idx)


class InstalledBuildMetadataTests(unittest.TestCase):
    def test_writes_installed_build_json(self) -> None:
        s = _script()
        self.assertIn('"$APP_HOME/.installed-build.json"', s)
        for key in (
            "artifact_sha256",
            "git_commit",
            "channel",
            "install_time_iso",
            "install_api",
            "package_url",
            "installer_url",
            "extracted_file_count",
        ):
            self.assertIn(f'"{key}":', s, msg=f"missing key {key} in installed-build JSON")

    def test_records_install_api(self) -> None:
        self.assertIn('"$APP_HOME/.install_api"', _script())


class PostInstallVerificationTests(unittest.TestCase):
    def test_imports_new_modules(self) -> None:
        s = _script()
        self.assertIn("agent.roblox_presence", s)
        self.assertIn("agent.freeform_enable", s)
        self.assertIn("agent.playing_state", s)
        self.assertIn("agent.dumpsys_cache", s)
        # Must exit non-zero on import failure.
        self.assertIn("Install verification failed: required modules did not import", s)

    def test_runs_version_and_compares_sha(self) -> None:
        s = _script()
        # The installer runs ``deng-rejoin version`` and greps for
        # ``artifact_sha256:`` to prove the wrapper executes the new code.
        self.assertIn('"$BIN/deng-rejoin" version', s)
        self.assertIn('grep -q "^artifact_sha256: "', s)
        self.assertIn("installed SHA mismatch", s)

    def test_wrapper_must_resolve_after_install(self) -> None:
        s = _script()
        self.assertIn("command -v deng-rejoin", s)
        self.assertIn("did not resolve after install", s)


class OrderingTests(unittest.TestCase):
    def test_sha_verify_before_purge(self) -> None:
        s = _script()
        sha = s.index("Package checksum mismatch")
        purge = s.index('rm -rf "$APP_HOME/$_d"')
        self.assertLess(sha, purge, msg="must SHA-verify before deleting old code")

    def test_purge_before_extract(self) -> None:
        s = _script()
        purge = s.index('rm -rf "$APP_HOME/$_d"')
        extract = s.index('tar -xzf "$TMP"')
        self.assertLess(purge, extract)

    def test_extract_before_installed_build_json(self) -> None:
        s = _script()
        extract = s.index('tar -xzf "$TMP"')
        # The metadata file is *written* by the heredoc `cat > ...
        # .installed-build.json`.  It also appears earlier in the purge
        # `rm -f` clause; find the write line specifically.
        meta = s.index('cat > "$APP_HOME/.installed-build.json"')
        self.assertLess(extract, meta)

    def test_version_check_before_install_complete(self) -> None:
        s = _script()
        version_check = s.index('"$BIN/deng-rejoin" version')
        complete = s.index("Install complete.")
        self.assertLess(version_check, complete)


if __name__ == "__main__":
    unittest.main()
