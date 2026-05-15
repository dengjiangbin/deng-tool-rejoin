"""Protected bootstrap GET /install/* and POST /api/install/authorize."""

from __future__ import annotations

import gzip
import io
import json
import os
import shutil
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


def _tmp_store_with_redeemed_key() -> tuple[LocalJsonLicenseStore, str]:
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    Path(tmp.name).unlink(missing_ok=True)
    store = LocalJsonLicenseStore(Path(tmp.name))
    uid = "u-install-test"
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
        self.assertIn('REQUESTED="latest"', text)
        self.assertNotIn("GITHUB_TOKEN", text)
        self.assertNotIn("LICENSE_KEY_EXPORT_SECRET", text)

    def test_pinned_version_bootstrap(self) -> None:
        status, _, body = _wsgi_call("GET", "/install/v1.0.0")
        self.assertEqual(status, 200)
        self.assertIn('REQUESTED="v1.0.0"', body.decode("utf-8"))

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
        self.assertNotIn('REQUESTED="latest"', body.decode("utf-8"))


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
