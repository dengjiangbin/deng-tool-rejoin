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


def _latest_script(sha: str = "a" * 64) -> str:
    return render_direct_install_bootstrap(
        base_url="https://rejoin.deng.my.id",
        package_sha256=sha,
        version_label="v1.0.0",
        channel="stable",
        requested_channel="latest",
        token_endpoint="/install/latest/package-token",
        installer_endpoint="/install/latest",
    )


class CacheBustingTests(unittest.TestCase):
    def test_curl_uses_cache_bust_query_param(self) -> None:
        s = _script()
        self.assertIn('c="$(date +%s)-$$"', s)
        self.assertIn("?t=$c", s)
        # Also send the canonical no-cache request headers.
        self.assertIn('-H "Cache-Control: no-cache"', s)
        self.assertIn('-H "Pragma: no-cache"', s)

    def test_keeps_cloudflare_safe_ua(self) -> None:
        # The UA bypasses Cloudflare's Browser Integrity Check; keep it.
        self.assertIn("deng-rejoin-installer/2.0", _script())

    def test_colorful_secure_sections_present(self) -> None:
        s = _script()
        # Simplified installer output (user request p-1bc476d931): only the
        # essential progress steps are echoed; the verbose "Verifying.../verified"
        # spam was removed.  The underlying integrity commands still run (asserted
        # in test_simplified_output_keeps_integrity_checks).
        for text in (
            "DENG Tool: Rejoin Installer",
            "Version: main-dev",
            "Preparing secure download",
            "Downloading protected package",
            "Package downloaded",
            "Installing files",
            "Files installed",
            "Install complete.",
            "Run: deng-rejoin",
        ):
            self.assertIn(text, s)
        # The removed verbose verification chatter must NOT reappear.
        for noise in (
            "Requesting one-time package token",
            "Token accepted",
            "Verifying archive SHA256",
            "Archive verified",
            "Verifying signed manifest",
            "Verifying runtime integrity",
            "Manifest signature verified",
            "Runtime verified",
            "Monitor runtime verified",
        ):
            self.assertNotIn(noise, s)
        self.assertIn("\\033[1;96m", s)
        self.assertIn("\\033[1;94m", s)
        self.assertIn("\\033[1;93m", s)
        self.assertIn("\\033[1;92m", s)
        self.assertIn("\\033[1;91m", s)
        self.assertIn("=" * 30, s)
        self.assertIn("-" * 30, s)

    def test_simplified_output_keeps_integrity_checks(self) -> None:
        # Even though the progress chatter was trimmed, the actual SHA256 match,
        # protected-runtime verification, and persistent-worker check must remain.
        s = _script()
        self.assertIn('[ "$a" = "$s" ]', s)
        self.assertIn("manifest or runtime integrity check failed", s)
        self.assertIn("persistent_worker", s)

    def test_no_permanent_package_url_or_token_leak(self) -> None:
        s = _script()
        self.assertIn("/install/test/package-token", s)
        self.assertIn("/api/download/package/", s)
        self.assertNotIn("/install/test/package.tar.gz", s)
        self.assertNotIn('echo "$p"', s)
        self.assertNotIn("GITHUB_TOKEN", s)
        self.assertNotIn("LICENSE_KEY_EXPORT_SECRET", s)


class PurgeStepTests(unittest.TestCase):
    def test_purges_known_code_directories(self) -> None:
        s = _script()
        self.assertIn('for d in "$h"/a?ent', s)
        self.assertIn('rm -rf "$d"', s)

    def test_purges_pycache_recursively(self) -> None:
        s = _script()
        self.assertIn('find "$h" -depth -name __pycache__ -type d', s)
        self.assertIn('find "$h" -name "*.pyc"', s)

    def test_removes_previous_build_metadata(self) -> None:
        s = _script()
        self.assertIn('rm -f "$h/BUILD-INFO.json"', s)
        self.assertIn('"$h/.installed-build.json"', s)

    def test_stops_running_processes_before_install(self) -> None:
        s = _script()
        self.assertIn('pkill -f "agent/deng_tool_rejoin.py"', s)
        self.assertNotIn("data/rejoin.pid", s)


class ShaVerifyTests(unittest.TestCase):
    def test_aborts_on_sha_mismatch(self) -> None:
        s = _script()
        self.assertIn('s="', s)
        self.assertIn('a="$(python3 -c', s)
        self.assertIn("hashlib.sha256", s)
        self.assertIn("Package checksum mismatch", s)
        # SHA check must come before extraction.
        sha_idx = s.index("Package checksum mismatch")
        extract_idx = s.index('tar -xzf "$t"')
        self.assertLess(sha_idx, extract_idx)


class InstalledBuildMetadataTests(unittest.TestCase):
    def test_writes_installed_build_json(self) -> None:
        s = _script()
        self.assertIn('"$h/.installed-build.json"', s)
        for key in (
            "artifact_sha256",
            "git_commit",
            "version",
            "channel",
            "install_time_iso",
            "install_api",
            "package_url",
            "installer_url",
            "extracted_file_count",
        ):
            self.assertIn(f'"{key}":', s, msg=f"missing key {key} in installed-build JSON")

    def test_latest_records_requested_channel_and_resolved_version(self) -> None:
        s = _latest_script()
        self.assertIn('"requested_channel": "latest"', s)
        self.assertIn('"resolved_version": "v1.0.0"', s)
        self.assertIn('"installer_url": "$u/install/latest"', s)

    def test_records_install_api(self) -> None:
        self.assertIn('"$h/.install_api"', _script())


class PostInstallVerificationTests(unittest.TestCase):
    def test_imports_new_modules(self) -> None:
        s = _script()
        self.assertIn("agent.roblox_presence", s)
        self.assertIn("agent.freeform_enable", s)
        self.assertIn("agent.playing_state", s)
        self.assertIn("agent.dumpsys_cache", s)
        # Must exit non-zero on import failure.
        self.assertIn("Install verification failed: manifest or runtime integrity check failed", s)

    def test_runs_version_and_compares_sha(self) -> None:
        s = _script()
        # The installer runs ``deng-rejoin version`` and greps for
        # ``artifact_sha256:`` to prove the wrapper executes the new code.
        self.assertIn('"$BIN/deng-rejoin" version', s)
        self.assertIn('grep -q "^artifact_sha256: "', s)
        self.assertIn("installed SHA mismatch", s)

    def test_wrapper_resolution_diagnostics_stay_out_of_installer(self) -> None:
        s = _script()
        self.assertNotIn("command -v deng-rejoin", s)
        self.assertNotIn("did not resolve after install", s)


class OrderingTests(unittest.TestCase):
    def test_sha_verify_before_purge(self) -> None:
        s = _script()
        sha = s.index("Package checksum mismatch")
        purge = s.index('rm -rf "$d"')
        self.assertLess(sha, purge, msg="must SHA-verify before deleting old code")

    def test_purge_before_extract(self) -> None:
        s = _script()
        purge = s.index('rm -rf "$d"')
        extract = s.index('tar -xzf "$t"')
        self.assertLess(purge, extract)

    def test_extract_before_installed_build_json(self) -> None:
        s = _script()
        extract = s.index('tar -xzf "$t"')
        # The metadata file is *written* by the heredoc `cat > ...
        # .installed-build.json`.  It also appears earlier in the purge
        # `rm -f` clause; find the write line specifically.
        meta = s.index('cat > "$h/.installed-build.json"')
        self.assertLess(extract, meta)

    def test_version_check_before_install_complete(self) -> None:
        s = _script()
        version_check = s.index('"$BIN/deng-rejoin" version')
        complete = s.index("Install complete.")
        self.assertLess(version_check, complete)


if __name__ == "__main__":
    unittest.main()
