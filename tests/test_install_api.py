"""Protected bootstrap GET /install/* and POST /api/install/authorize."""

from __future__ import annotations

import gzip
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import bot.license_api as api_mod
from agent.license_store import LocalJsonLicenseStore


def _wsgi_call(method: str, path: str, body=None, environ_extra: dict | None = None):
    import io as io_mod

    body_bytes = b""
    if body is None:
        body_bytes = b""
    elif isinstance(body, dict):
        body_bytes = json.dumps(body).encode()
    else:
        body_bytes = body

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(body_bytes)),
        "wsgi.input": io_mod.BytesIO(body_bytes),
        "REMOTE_ADDR": "127.0.0.1",
        "QUERY_STRING": "",
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8787",
    }
    if environ_extra:
        environ.update(environ_extra)

    captured_status: list[str] = []
    captured_headers: list[tuple[str, str]] = []

    def start_response(status: str, headers: list):
        captured_status.append(status)
        captured_headers.extend(headers)

    chunks = api_mod._wsgi_app(environ, start_response)
    body_out = b"".join(chunks)
    status_int = int(captured_status[0].split(" ")[0]) if captured_status else 0
    headers_dict = dict(captured_headers)
    return status_int, headers_dict, body_out


def _tmp_store_with_redeemed_key(uid: str = "u-install-test") -> tuple[LocalJsonLicenseStore, str]:
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    Path(tmp.name).unlink(missing_ok=True)
    store = LocalJsonLicenseStore(Path(tmp.name))
    store.get_or_create_user(uid)
    key = store.create_key_for_user(uid)
    return store, key


def _write_versions_manifest(path: Path, rows: list) -> None:
    path.write_text(json.dumps(rows), encoding="utf-8")


def _make_tarball(agent_stub: bool = True) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        if agent_stub:
            data = b"# stub\n"
            ti = tarfile.TarInfo(name="agent/deng_tool_rejoin.py")
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


class InstallBootstrapGetTests(unittest.TestCase):
    def test_latest_returns_shell_and_title(self) -> None:
        status, headers, body = _wsgi_call("GET", "/install/latest")
        self.assertEqual(status, 200)
        self.assertIn("text/", headers.get("Content-Type", ""))
        text = body.decode("utf-8")
        self.assertIn("DENG Tool: Rejoin Installer", text)
        self.assertIn('printf \'%s\\n\' "latest"', text)
        self.assertIn(".install_requested", text)
        self.assertIn("Next: run deng-rejoin", text)
        self.assertNotIn("GITHUB_TOKEN", text)
        self.assertNotIn("LICENSE_KEY_EXPORT_SECRET", text)

    def test_pinned_version_bootstrap(self) -> None:
        status, _, body = _wsgi_call("GET", "/install/v1.0.0")
        self.assertEqual(status, 200)
        self.assertIn('printf \'%s\\n\' "v1.0.0"', body.decode("utf-8"))

    def test_latest_resolves_registry_semantics_separate_test_manifest(self) -> None:
        manifest = Path(__file__).resolve().parent / "_tmp_install_manifest_latest.json"
        root = Path(tempfile.mkdtemp())
        try:
            artifact_rel = "releases/v1.0.1/pkg.tar.gz"
            (root / Path(artifact_rel).parent).mkdir(parents=True)
            (root / artifact_rel).write_bytes(gzip.compress(b"x"))
            rows = [
                {
                    "version": "v1.0.0",
                    "channel": "stable",
                    "visibility": "public",
                    "install_ref": "refs/tags/v1.0.0",
                    "artifact_path": "releases/v1.0.0/pkg.tar.gz",
                    "artifact_sha256": "",
                    "enabled": True,
                },
                {
                    "version": "v1.0.1",
                    "channel": "stable",
                    "visibility": "public",
                    "install_ref": "refs/tags/v1.0.1",
                    "artifact_path": artifact_rel,
                    "artifact_sha256": "",
                    "enabled": True,
                },
            ]
            _write_versions_manifest(manifest, rows)
            (root / "releases/v1.0.0").mkdir(parents=True)
            (root / "releases/v1.0.0/pkg.tar.gz").write_bytes(gzip.compress(b"y"))

            env = {
                "REJOIN_VERSIONS_MANIFEST": str(manifest),
                "REJOIN_ARTIFACT_ROOT": str(root),
            }
            with patch.dict(os.environ, env, clear=False):
                store, key = _tmp_store_with_redeemed_key()
                with patch("agent.license_store.get_default_store", return_value=store):
                    st, _, resp = _wsgi_call(
                        "POST",
                        "/api/install/authorize",
                        {
                            "license_key": key,
                            "requested_version": "latest",
                            "install_id_hash": "",
                        },
                    )
                    self.assertEqual(st, 200, resp)
                    data = json.loads(resp)
                    self.assertEqual(data["result"], "active")
                    self.assertEqual(data["resolved_version"], "v1.0.1")

                    st2, _, resp2 = _wsgi_call(
                        "POST",
                        "/api/install/authorize",
                        {
                            "license_key": key,
                            "requested_version": "v1.0.0",
                            "install_id_hash": "",
                        },
                    )
                    self.assertEqual(st2, 200, resp2)
                    data2 = json.loads(resp2)
                    self.assertEqual(data2["resolved_version"], "v1.0.0")
        finally:
            manifest.unlink(missing_ok=True)
            shutil.rmtree(root, ignore_errors=True)

    def test_pinned_v100_not_latest_when_latest_moves(self) -> None:
        """Regression: /install/v1.0.0 bootstrap still pins REQUESTED=v1.0.0."""
        _, _, body = _wsgi_call("GET", "/install/v1.0.0")
        self.assertNotIn('"latest"', body.decode("utf-8"))


class InstallAuthorizeLicenseTests(unittest.TestCase):
    def setUp(self) -> None:
        with api_mod._rate_limit_lock:
            api_mod._rate_limit.clear()

    def test_unredeemed_key_rejected(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        store = LocalJsonLicenseStore(Path(tmp.name))
        uid = "owner"
        store.get_or_create_user(uid)
        key = store.create_key_for_user(uid)
        # not redeemed — remove owner_discord_id manually from underlying db not trivial;
        # create_key binds owner — use fake invalid approach: corrupt store record
        db = store._load()
        kh = next(iter(db["keys"]))
        db["keys"][kh]["owner_discord_id"] = None
        store._save(db)

        manifest = Path(__file__).resolve().parent / "_tmp_install_manifest_nr.json"
        root = Path(tempfile.mkdtemp())
        try:
            rows = [
                {
                    "version": "v1.0.0",
                    "channel": "stable",
                    "visibility": "public",
                    "install_ref": "refs/tags/v1.0.0",
                    "artifact_path": "releases/v1.0.0/pkg.tar.gz",
                    "artifact_sha256": "",
                    "enabled": True,
                }
            ]
            _write_versions_manifest(manifest, rows)
            (root / "releases/v1.0.0").mkdir(parents=True)
            (root / "releases/v1.0.0/pkg.tar.gz").write_bytes(_make_tarball())

            env = {"REJOIN_VERSIONS_MANIFEST": str(manifest), "REJOIN_ARTIFACT_ROOT": str(root)}
            with patch.dict(os.environ, env, clear=False):
                with patch("agent.license_store.get_default_store", return_value=store):
                    st, _, resp = _wsgi_call(
                        "POST",
                        "/api/install/authorize",
                        {
                            "license_key": key,
                            "requested_version": "v1.0.0",
                            "install_id_hash": "",
                        },
                    )
                self.assertEqual(st, 403)
                data = json.loads(resp)
                self.assertEqual(data["result"], "key_not_redeemed")
        finally:
            manifest.unlink(missing_ok=True)
            shutil.rmtree(root, ignore_errors=True)
            Path(store._path).unlink(missing_ok=True)

    def test_owned_unbound_allowed(self) -> None:
        manifest = Path(__file__).resolve().parent / "_tmp_install_manifest_ok.json"
        root = Path(tempfile.mkdtemp())
        try:
            rows = [
                {
                    "version": "v1.0.0",
                    "channel": "stable",
                    "visibility": "public",
                    "install_ref": "refs/tags/v1.0.0",
                    "artifact_path": "releases/v1.0.0/pkg.tar.gz",
                    "artifact_sha256": "",
                    "enabled": True,
                }
            ]
            _write_versions_manifest(manifest, rows)
            (root / "releases/v1.0.0").mkdir(parents=True)
            (root / "releases/v1.0.0/pkg.tar.gz").write_bytes(_make_tarball())

            store, key = _tmp_store_with_redeemed_key()
            env = {"REJOIN_VERSIONS_MANIFEST": str(manifest), "REJOIN_ARTIFACT_ROOT": str(root)}
            with patch.dict(os.environ, env, clear=False):
                with patch("agent.license_store.get_default_store", return_value=store):
                    st, _, resp = _wsgi_call(
                        "POST",
                        "/api/install/authorize",
                        {
                            "license_key": key,
                            "requested_version": "v1.0.0",
                            "install_id_hash": "",
                        },
                    )
            self.assertEqual(st, 200, resp)
            data = json.loads(resp)
            self.assertEqual(data["result"], "active")
            self.assertIn("/api/download/package/", data["download_url"])
        finally:
            manifest.unlink(missing_ok=True)
            shutil.rmtree(root, ignore_errors=True)

    def test_wrong_device_blocked_when_bound(self) -> None:
        manifest = Path(__file__).resolve().parent / "_tmp_install_manifest_wd.json"
        root = Path(tempfile.mkdtemp())
        try:
            rows = [
                {
                    "version": "v1.0.0",
                    "channel": "stable",
                    "visibility": "public",
                    "install_ref": "refs/tags/v1.0.0",
                    "artifact_path": "releases/v1.0.0/pkg.tar.gz",
                    "artifact_sha256": "",
                    "enabled": True,
                }
            ]
            _write_versions_manifest(manifest, rows)
            (root / "releases/v1.0.0").mkdir(parents=True)
            (root / "releases/v1.0.0/pkg.tar.gz").write_bytes(_make_tarball())

            store, key = _tmp_store_with_redeemed_key()
            h = "a" * 64
            store.bind_or_check_device(key, h, "Pixel", "1.0.0")

            env = {"REJOIN_VERSIONS_MANIFEST": str(manifest), "REJOIN_ARTIFACT_ROOT": str(root)}
            with patch.dict(os.environ, env, clear=False):
                with patch("agent.license_store.get_default_store", return_value=store):
                    st, _, resp = _wsgi_call(
                        "POST",
                        "/api/install/authorize",
                        {
                            "license_key": key,
                            "requested_version": "v1.0.0",
                            "install_id_hash": "",
                        },
                    )
            self.assertEqual(st, 403)
            self.assertEqual(json.loads(resp)["result"], "wrong_device")
        finally:
            manifest.unlink(missing_ok=True)
            shutil.rmtree(root, ignore_errors=True)


class InternalDevBootstrapTests(unittest.TestCase):
    def test_dev_main_requires_signature(self) -> None:
        status, _, body = _wsgi_call("GET", "/install/dev/main")
        self.assertEqual(status, 403)

    def test_main_dev_authorize_requires_session(self) -> None:
        manifest = Path(__file__).resolve().parent / "_tmp_install_manifest_main.json"
        root = Path(tempfile.mkdtemp())
        try:
            rows = [
                {
                    "version": "main-dev",
                    "channel": "dev",
                    "visibility": "admin",
                    "install_ref": "refs/heads/main",
                    "artifact_path": "dev/pkg.tar.gz",
                    "artifact_sha256": "",
                    "enabled": True,
                }
            ]
            _write_versions_manifest(manifest, rows)
            (root / "dev").mkdir(parents=True)
            (root / "dev/pkg.tar.gz").write_bytes(_make_tarball())

            store, key = _tmp_store_with_redeemed_key()
            env = {"REJOIN_VERSIONS_MANIFEST": str(manifest), "REJOIN_ARTIFACT_ROOT": str(root)}
            with patch.dict(os.environ, env, clear=False):
                with patch("agent.license_store.get_default_store", return_value=store):
                    st, _, resp = _wsgi_call(
                        "POST",
                        "/api/install/authorize",
                        {
                            "license_key": key,
                            "requested_version": "main-dev",
                            "install_id_hash": "",
                        },
                    )
            self.assertEqual(st, 403)
            self.assertEqual(json.loads(resp)["result"], "forbidden")
        finally:
            manifest.unlink(missing_ok=True)
            shutil.rmtree(root, ignore_errors=True)


class InstallTestLatestBootstrapTests(unittest.TestCase):
    def test_test_latest_returns_shell_and_banner(self) -> None:
        status, headers, body = _wsgi_call("GET", "/install/test/latest")
        self.assertEqual(status, 200)
        self.assertIn("text/", headers.get("Content-Type", ""))
        text = body.decode("utf-8")
        self.assertIn("DENG Tool: Rejoin Test Installer", text)
        self.assertIn("Channel: internal test", text)
        self.assertIn("Version: main-dev", text)
        self.assertIn('printf \'%s\\n\' "test-latest"', text)
        self.assertIn(".install_requested", text)
        self.assertNotIn("GITHUB_TOKEN", text)
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", text)
        self.assertNotIn("LICENSE_KEY_EXPORT_SECRET", text)

    def test_beta_latest_redirects_to_test_latest(self) -> None:
        status, headers, body = _wsgi_call("GET", "/install/beta/latest")
        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "/install/test/latest")
        self.assertEqual(body, b"")


class InstallBootstrapSanityTests(unittest.TestCase):
    """Non-interactive bootstrap + launcher tarball."""

    def test_test_latest_has_no_forbidden_install_strings(self) -> None:
        _, _, body = _wsgi_call("GET", "/install/test/latest")
        text = body.decode("utf-8")
        tl = text.lower()
        self.assertNotIn("license key", tl)
        self.assertNotIn("hidden", tl)
        self.assertNotIn("read -s", tl)
        self.assertNotIn("supabase_service_role_key", tl)
        self.assertNotIn("raw.githubusercontent.com", tl)

    def test_public_bootstrap_passes_bash_n(self) -> None:
        from agent.bootstrap_installer import render_public_bootstrap

        bash_exe = shutil.which("bash")
        if not bash_exe:
            self.skipTest("bash not on PATH")
        script = render_public_bootstrap(
            base_url="https://rejoin.deng.my.id",
            requested="latest",
        )
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".sh", delete=False) as tmp:
            tmp.write(script.encode("utf-8"))
            tmp_path = tmp.name
        try:
            proc = subprocess.run(
                [bash_exe, "-n", tmp_path],
                capture_output=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr.decode("utf-8", errors="replace"))
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_launcher_bundle_tarball_served(self) -> None:
        status, headers, body = _wsgi_call("GET", "/install/launcher/bundle.tar.gz")
        self.assertEqual(status, 200)
        self.assertIn("gzip", headers.get("Content-Type", "").lower())
        self.assertGreater(len(body), 64)
        buf = io.BytesIO(body)
        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            names = tf.getnames()
        self.assertIn("agent/deferred_bundle_install.py", names)
        self.assertIn("agent/deng_tool_rejoin.py", names)
        self.assertIn("agent/__init__.py", names)

    def test_bootstrap_prefers_prefix_bin_avoids_local_bin(self) -> None:
        from agent.bootstrap_installer import render_public_bootstrap

        s = render_public_bootstrap(base_url="https://rejoin.deng.my.id", requested="latest")
        self.assertIn('BIN="${PREFIX}/bin"', s)
        self.assertIn('mkdir -p "${PREFIX}/bin"', s)
        self.assertIn("$HOME/bin", s)
        self.assertNotIn("$HOME/.local/bin", s)
        self.assertIn("command -v deng-rejoin", s)
        self.assertIn("hash -r", s)
        self.assertIn("Failed to create deng-rejoin command.", s)
        self.assertIn('.install_api', s)
        self.assertIn(
            'printf \'%s\\n\' "$DENG_REJOIN_INSTALL_API" > "$APP_HOME/.install_api"',
            s,
        )
        self.assertIn(
            'export DENG_REJOIN_INSTALL_API="${DENG_REJOIN_INSTALL_API:-https://rejoin.deng.my.id}"',
            s,
        )
        self.assertIn("rejoin.deng.my.id", s)

    def test_install_complete_only_after_command_check(self) -> None:
        from agent.bootstrap_installer import render_public_bootstrap

        s = render_public_bootstrap(base_url="https://x.example", requested="test-latest")
        done = s.index("Install complete.")
        self.assertLess(s.index("command -v deng-rejoin"), done)
        self.assertLess(s.index("resolve_install_api"), done)
        self.assertLess(s.index(".install_api"), done)


class InstallTermuxSimulationTests(unittest.TestCase):
    """Fake HOME/PREFIX + stub curl to exercise the public bootstrap without network."""

    def _require_bash(self) -> str | None:
        b = shutil.which("bash")
        if not b:
            self.skipTest("bash not on PATH")
        return b

    def test_simulated_termux_install_then_deng_rejoin_no_missing_api_error(self) -> None:
        self._require_bash()
        from agent.bootstrap_installer import render_public_bootstrap

        tar_path = PROJECT / "releases" / "launcher" / "deng-rejoin-launcher.tar.gz"
        if not tar_path.is_file():
            self.skipTest("launcher tarball missing; run scripts/build_launcher_bundle.py")

        td = tempfile.mkdtemp()
        try:
            home = Path(td) / "h"
            prefix = Path(td) / "p"
            home.mkdir()
            (prefix / "bin").mkdir(parents=True)
            bindir = Path(td) / "fakebin"
            bindir.mkdir()

            curl_sh = bindir / "curl"
            curl_sh.write_text(
                f"#!/bin/sh\n"
                f'REJOIN_SIM_TARBALL="{tar_path.as_posix()}"\n'
                'out=""\n'
                "while [ $# -gt 0 ]; do\n"
                '  if [ "$1" = "-o" ]; then shift; out="$1"; break; fi\n'
                "  shift\n"
                "done\n"
                '[ -n "$out" ] || exit 1\n'
                'cp "$REJOIN_SIM_TARBALL" "$out"\n',
                encoding="utf-8",
                newline="\n",
            )
            os.chmod(curl_sh, 0o755)

            script = render_public_bootstrap(
                base_url="https://rejoin.deng.my.id",
                requested="latest",
            )
            install_sh = Path(td) / "install.sh"
            install_sh.write_text(script, encoding="utf-8", newline="\n")

            env = os.environ.copy()
            env["HOME"] = str(home)
            env["PREFIX"] = str(prefix)
            env["PATH"] = f"{bindir}:{prefix / 'bin'}:/usr/bin:/bin"
            env["PYTHONUNBUFFERED"] = "1"
            env.pop("DENG_REJOIN_INSTALL_API", None)

            r = subprocess.run(
                ["bash", str(install_sh)],
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(r.returncode, 0, msg=r.stderr + r.stdout)
            self.assertIn("Install complete.", r.stdout)

            which_r = subprocess.run(
                ["bash", "-c", 'command -v deng-rejoin'],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(which_r.returncode, 0, msg=which_r.stderr)
            self.assertEqual(
                which_r.stdout.strip(),
                str(prefix / "bin" / "deng-rejoin"),
            )

            env2 = env.copy()
            env2.pop("DENG_REJOIN_INSTALL_API", None)
            r2 = subprocess.run(
                ["bash", str(prefix / "bin" / "deng-rejoin")],
                env=env2,
                input="",
                capture_output=True,
                text=True,
                timeout=15,
            )
            combo = r2.stderr + r2.stdout
            self.assertNotIn("DENG_REJOIN_INSTALL_API is not set", combo)
        finally:
            shutil.rmtree(td, ignore_errors=True)


class InstallTestLatestAuthorizeTests(unittest.TestCase):
    def setUp(self) -> None:
        with api_mod._rate_limit_lock:
            api_mod._rate_limit.clear()

    def _main_dev_manifest_env(self) -> tuple[Path, Path]:
        manifest = Path(__file__).resolve().parent / "_tmp_install_manifest_test_latest.json"
        root = Path(tempfile.mkdtemp())
        rows = [
            {
                "version": "main-dev",
                "channel": "dev",
                "visibility": "admin",
                "install_ref": "refs/heads/main",
                "artifact_path": "dev/pkg.tar.gz",
                "artifact_sha256": "",
                "enabled": True,
            }
        ]
        _write_versions_manifest(manifest, rows)
        (root / "dev").mkdir(parents=True)
        (root / "dev/pkg.tar.gz").write_bytes(_make_tarball())
        return manifest, root

    def test_owner_license_can_authorize(self) -> None:
        manifest, root = self._main_dev_manifest_env()
        owner_uid = "333333333333333333"
        store, key = _tmp_store_with_redeemed_key(owner_uid)
        env = {
            "REJOIN_VERSIONS_MANIFEST": str(manifest),
            "REJOIN_ARTIFACT_ROOT": str(root),
            "LICENSE_OWNER_DISCORD_IDS": owner_uid,
            "REJOIN_TESTER_DISCORD_IDS": "",
        }
        try:
            with patch.dict(os.environ, env, clear=False):
                from agent.install_internal_access import clear_install_internal_access_cache

                clear_install_internal_access_cache()
                with patch("agent.license_store.get_default_store", return_value=store):
                    st, _, resp = _wsgi_call(
                        "POST",
                        "/api/install/authorize",
                        {
                            "license_key": key,
                            "requested_version": "test-latest",
                            "install_id_hash": "",
                        },
                    )
            self.assertEqual(st, 200, resp)
            data = json.loads(resp)
            self.assertEqual(data["result"], "active")
            self.assertEqual(data["resolved_version"], "main-dev")
            self.assertIn("/api/download/package/", data["download_url"])
        finally:
            manifest.unlink(missing_ok=True)
            shutil.rmtree(root, ignore_errors=True)
            Path(store._path).unlink(missing_ok=True)

    def test_tester_license_can_authorize(self) -> None:
        manifest, root = self._main_dev_manifest_env()
        tester_uid = "444444444444444444"
        store, key = _tmp_store_with_redeemed_key(tester_uid)
        env = {
            "REJOIN_VERSIONS_MANIFEST": str(manifest),
            "REJOIN_ARTIFACT_ROOT": str(root),
            "LICENSE_OWNER_DISCORD_IDS": "",
            "REJOIN_TESTER_DISCORD_IDS": tester_uid,
        }
        try:
            with patch.dict(os.environ, env, clear=False):
                from agent.install_internal_access import clear_install_internal_access_cache

                clear_install_internal_access_cache()
                with patch("agent.license_store.get_default_store", return_value=store):
                    st, _, resp = _wsgi_call(
                        "POST",
                        "/api/install/authorize",
                        {
                            "license_key": key,
                            "requested_version": "test-latest",
                            "install_id_hash": "",
                        },
                    )
            self.assertEqual(st, 200, resp)
            self.assertEqual(json.loads(resp)["resolved_version"], "main-dev")
        finally:
            manifest.unlink(missing_ok=True)
            shutil.rmtree(root, ignore_errors=True)
            Path(store._path).unlink(missing_ok=True)

    def test_public_redeemed_license_cannot_authorize(self) -> None:
        manifest, root = self._main_dev_manifest_env()
        store, key = _tmp_store_with_redeemed_key("555555555555555555")
        env = {
            "REJOIN_VERSIONS_MANIFEST": str(manifest),
            "REJOIN_ARTIFACT_ROOT": str(root),
            "LICENSE_OWNER_DISCORD_IDS": "999999999999999999",
            "REJOIN_TESTER_DISCORD_IDS": "",
        }
        try:
            with patch.dict(os.environ, env, clear=False):
                from agent.install_internal_access import clear_install_internal_access_cache

                clear_install_internal_access_cache()
                with patch("agent.license_store.get_default_store", return_value=store):
                    st, _, resp = _wsgi_call(
                        "POST",
                        "/api/install/authorize",
                        {
                            "license_key": key,
                            "requested_version": "test-latest",
                            "install_id_hash": "",
                        },
                    )
            self.assertEqual(st, 403)
            data = json.loads(resp)
            self.assertEqual(data["result"], "forbidden")
            self.assertEqual(data["message"], api_mod._TEST_INSTALL_FORBIDDEN_MESSAGE)
        finally:
            manifest.unlink(missing_ok=True)
            shutil.rmtree(root, ignore_errors=True)
            Path(store._path).unlink(missing_ok=True)

    def test_unredeemed_key_rejected(self) -> None:
        manifest, root = self._main_dev_manifest_env()
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        store = LocalJsonLicenseStore(Path(tmp.name))
        uid = "666666666666666666"
        store.get_or_create_user(uid)
        key = store.create_key_for_user(uid)
        db = store._load()
        kh = next(iter(db["keys"]))
        db["keys"][kh]["owner_discord_id"] = None
        store._save(db)

        env = {
            "REJOIN_VERSIONS_MANIFEST": str(manifest),
            "REJOIN_ARTIFACT_ROOT": str(root),
            "LICENSE_OWNER_DISCORD_IDS": uid,
            "REJOIN_TESTER_DISCORD_IDS": "",
        }
        try:
            with patch.dict(os.environ, env, clear=False):
                from agent.install_internal_access import clear_install_internal_access_cache

                clear_install_internal_access_cache()
                with patch("agent.license_store.get_default_store", return_value=store):
                    st, _, resp = _wsgi_call(
                        "POST",
                        "/api/install/authorize",
                        {
                            "license_key": key,
                            "requested_version": "test-latest",
                            "install_id_hash": "",
                        },
                    )
            self.assertEqual(st, 403)
            self.assertEqual(json.loads(resp)["result"], "key_not_redeemed")
        finally:
            manifest.unlink(missing_ok=True)
            shutil.rmtree(root, ignore_errors=True)
            Path(store._path).unlink(missing_ok=True)

    def test_wrong_device_blocked_when_bound(self) -> None:
        manifest, root = self._main_dev_manifest_env()
        owner_uid = "777777777777777777"
        store, key = _tmp_store_with_redeemed_key(owner_uid)
        h = "a" * 64
        store.bind_or_check_device(key, h, "Pixel", "1.0.0")

        env = {
            "REJOIN_VERSIONS_MANIFEST": str(manifest),
            "REJOIN_ARTIFACT_ROOT": str(root),
            "LICENSE_OWNER_DISCORD_IDS": owner_uid,
        }
        try:
            with patch.dict(os.environ, env, clear=False):
                from agent.install_internal_access import clear_install_internal_access_cache

                clear_install_internal_access_cache()
                with patch("agent.license_store.get_default_store", return_value=store):
                    st, _, resp = _wsgi_call(
                        "POST",
                        "/api/install/authorize",
                        {
                            "license_key": key,
                            "requested_version": "test-latest",
                            "install_id_hash": "",
                        },
                    )
            self.assertEqual(st, 403)
            self.assertEqual(json.loads(resp)["result"], "wrong_device")
        finally:
            manifest.unlink(missing_ok=True)
            shutil.rmtree(root, ignore_errors=True)
            Path(store._path).unlink(missing_ok=True)


class PanelCopyUsesProtectedUrlTests(unittest.TestCase):
    def test_public_stable_copy_uses_rejoin_domain(self) -> None:
        from agent import rejoin_versions as rv

        info = rv.RejoinVersionInfo(
            version="v1.0.0",
            channel="stable",
            label="v1.0.0 Stable",
            install_ref="refs/tags/v1.0.0",
        )
        manifest = Path(__file__).resolve().parent / "_tmp_panel_pub.json"
        _write_versions_manifest(
            manifest,
            [
                {
                    "version": "v1.0.0",
                    "channel": "stable",
                    "visibility": "public",
                    "install_ref": "refs/tags/v1.0.0",
                    "enabled": True,
                },
                {
                    "version": "v1.0.1",
                    "channel": "stable",
                    "visibility": "public",
                    "install_ref": "refs/tags/v1.0.1",
                    "enabled": True,
                },
            ],
        )
        try:
            with patch.dict(os.environ, {"REJOIN_VERSIONS_MANIFEST": str(manifest)}, clear=False):
                cmd = rv.build_public_install_curl_command(info)
            self.assertIn("rejoin.deng.my.id/install/v1.0.0", cmd)
            self.assertNotIn("raw.githubusercontent.com", cmd)
        finally:
            manifest.unlink(missing_ok=True)

    def test_internal_main_dev_uses_fixed_test_install_url(self) -> None:
        from agent import rejoin_versions as rv

        info = rv.RejoinVersionInfo(
            version="main-dev",
            channel="dev",
            label="main-dev",
            install_ref="refs/heads/main",
            internal_only=True,
        )
        manifest = Path(__file__).resolve().parent / "_tmp_panel_internal.json"
        _write_versions_manifest(manifest, [])
        try:
            with patch.dict(os.environ, {"REJOIN_VERSIONS_MANIFEST": str(manifest)}, clear=False):
                cmd = rv.build_public_install_curl_command(info)
            self.assertIn("rejoin.deng.my.id/install/test/latest", cmd)
            self.assertNotIn("install/dev/main", cmd)
            self.assertNotIn("sig=", cmd)
        finally:
            manifest.unlink(missing_ok=True)


class DocsNoRawGithubInstallTests(unittest.TestCase):
    def test_selected_public_docs_avoid_raw_github_install_path(self) -> None:
        root = PROJECT
        for rel in (
            "README.md",
            "docs/NEW_USER_TERMUX_GUIDE.md",
            "docs/PUBLIC_INSTALL.md",
            "docs/PUBLIC_USER_GUIDE.md",
            "docs/DISCORD_LICENSE_PANEL.md",
        ):
            text = (root / rel).read_text(encoding="utf-8")
            lowered = text.lower()
            self.assertNotIn("raw.githubusercontent.com", lowered, msg=rel)
            self.assertNotIn("deng-rejoin-update", lowered, msg=rel)


if __name__ == "__main__":
    unittest.main()
